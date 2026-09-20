"""Rubrics: AI generation grounded in course material, deterministic mark balancing, versioning, approval."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, Unprocessable
from app.db.session import session_scope
from app.models import Evaluation, Exam, Question, Rubric, RubricCriterion, RubricVersion
from app.models.enums import JobKind, RubricSource, RubricVersionStatus
from app.providers import registry
from app.schemas.rubrics import CriterionOut, RubricOut, RubricPut, UnitOut, VersionOut
from app.services import retrieval
from app.services.jobs import JobContext, JobError, JobResult, handler
from app.services.marks import Unit, q2, units_of

log = logging.getLogger("MarksWala.rubric")
PROMPT_VERSION = "rubric-gen-v1"


def _step_for(total: Decimal) -> Decimal:
    for step in (Decimal("0.5"), Decimal("0.25"), Decimal("0.01")):
        if total % step == 0:
            return step
    return Decimal("0.01")


class GenCriterion(BaseModel):
    title: str = Field(description="Short name of what is being assessed, e.g. 'States the precondition (sorted input)'")
    description: str = Field(description="What a full-marks response contains for this criterion")
    expected_points: str = Field(description="The concrete facts/steps a correct answer must mention, drawn from the course material")
    max_marks: float = Field(description="Marks for this criterion, in multiples of 0.5")


class GenRubric(BaseModel):
    criteria: list[GenCriterion] = Field(description="2 to 6 independent, checkable criteria")
    confidence: float = Field(ge=0, le=1, description="How well the course material supports this rubric (0..1)")


SYSTEM = (
    "You are an experienced university examiner writing a marking rubric. Produce independent, checkable criteria that "
    "together cover exactly what earns full marks, based on the course material provided. Split marks sensibly between "
    "conceptual correctness, explanation/derivation and examples/application as the question demands. The criteria's marks "
    "MUST add up to exactly the question's maximum marks, in multiples of 0.5. Do not require content that the course "
    "material does not support."
)


def balance_marks(raw: list[float], total: Decimal) -> tuple[list[Decimal], bool]:
    """Deterministically make criterion marks (multiples of 0.5, > 0) sum to exactly `total`.
    Returns (marks, adjusted). Never invents criteria; only rescales/rounds what the model produced."""
    if not raw:
        raise ValueError("no criteria")
    STEP = _step_for(total)
    vals = [max(STEP, (q2(v) / STEP).quantize(Decimal(1)) * STEP) for v in raw]
    adjusted = any(v != q2(r) for v, r in zip(vals, raw))
    s = sum(vals, Decimal(0))
    if s != total:
        adjusted = True
        # rescale proportionally, round to steps, then push the remainder onto the largest criteria
        vals = [max(STEP, (v * total / s / STEP).quantize(Decimal(1)) * STEP) for v in vals]
        diff = total - sum(vals, Decimal(0))
        order = sorted(range(len(vals)), key=lambda i: vals[i], reverse=diff > 0)
        guard = 0
        while diff != 0 and guard < 1000:
            for i in order:
                step = STEP if diff > 0 else -STEP
                if vals[i] + step >= STEP:
                    vals[i] += step
                    diff -= step
                    if diff == 0:
                        break
            guard += 1
        if diff != 0 or sum(vals, Decimal(0)) != total:
            raise ValueError("cannot balance")
    return vals, adjusted


def _unit_prompt(unit: Unit, question_text: str, parent_stem: str | None, chunks) -> str:
    ctx = "\n\n".join(f"[{c.resource_title}, {c.locator}] {c.text}" for c in chunks) or "(no matching course material was found)"
    stem = f"Parent question stem: {parent_stem}\n" if parent_stem else ""
    return (
        f"QUESTION {unit.label} — maximum marks: {unit.max_marks}\n{stem}Question: {question_text}\n\n"
        f"COURSE MATERIAL (retrieved):\n{ctx}\n\n"
        f"Write the rubric. The criteria marks must sum to exactly {unit.max_marks}."
    )


def _next_version_number(db: Session, rubric_id: uuid.UUID) -> int:
    return (db.scalar(select(func.max(RubricVersion.version_number)).where(RubricVersion.rubric_id == rubric_id)) or 0) + 1


def get_or_create_rubric(db: Session, exam_id: uuid.UUID) -> Rubric:
    rubric = db.scalar(select(Rubric).where(Rubric.exam_id == exam_id))
    if rubric is None:
        rubric = Rubric(exam_id=exam_id)
        db.add(rubric)
        db.flush()
    return rubric


def draft_version(db: Session, exam_id: uuid.UUID) -> RubricVersion | None:
    return db.scalar(
        select(RubricVersion).join(Rubric).where(Rubric.exam_id == exam_id, RubricVersion.status == RubricVersionStatus.DRAFT)
    )


def approved_version(db: Session, exam_id: uuid.UUID) -> RubricVersion | None:
    return db.scalar(
        select(RubricVersion).join(Rubric).where(Rubric.exam_id == exam_id, RubricVersion.status == RubricVersionStatus.APPROVED)
    )


@handler(JobKind.GENERATE_RUBRIC)
def generate_rubric(ctx: JobContext) -> JobResult:
    exam_id, user_id = ctx.entity_id, ctx.created_by
    ai, embedder, store = registry.get_ai(), registry.get_embedder(), registry.get_vector_store()

    with session_scope() as db:
        exam = db.get(Exam, exam_id)
        if exam is None or exam.deleted_at is not None:
            raise JobError("The exam no longer exists.", code="exam_missing")
        questions = list(db.scalars(select(Question).where(Question.exam_id == exam_id).order_by(Question.position)))
        units = units_of(questions)
        if not units:
            raise JobError("There are no questions to build a rubric for.", code="no_questions")
        stems = {q.id: q.text for q in questions}
        texts = {(u.question_id, u.subquestion_id): "" for u in units}
        for q in questions:
            if q.subquestions:
                for s in q.subquestions:
                    texts[(q.id, s.id)] = s.text
            else:
                texts[(q.id, None)] = q.text
        course_id = exam.course_id
        draft = draft_version(db, exam_id)
        if draft is None:  # first attempt for this job; a retry resumes the draft it already started
            rubric = get_or_create_rubric(db, exam_id)
            draft = RubricVersion(
                rubric_id=rubric.id, version_number=_next_version_number(db, rubric.id),
                status=RubricVersionStatus.DRAFT, source=RubricSource.AI, created_by=user_id, notes=f"generated by {ai.model} ({PROMPT_VERSION})",
            )
            db.add(draft)
        db.flush()
        version_id = draft.id
        done_units = {(c.question_id, c.subquestion_id) for c in db.scalars(select(RubricCriterion).where(RubricCriterion.rubric_version_id == version_id))}

    total = len(units)
    for i, unit in enumerate(units):
        key = (unit.question_id, unit.subquestion_id)
        if key in done_units:
            continue
        ctx.progress(i / total, f"Writing rubric for {unit.label} ({i + 1}/{total})")
        with session_scope() as db:
            chunks = retrieval.retrieve(db, course_id=course_id, query=f"{stems.get(unit.question_id, '')} {texts[key]}".strip(),
                                        embedder=embedder, store=store, top_k=4)
        retr_conf = retrieval.retrieval_confidence(chunks)
        parent = stems.get(unit.question_id) if unit.subquestion_id else None
        gen = ai.generate_structured(system=SYSTEM, prompt=_unit_prompt(unit, texts[key], parent or None, chunks), schema=GenRubric)
        crit = [c for c in gen.criteria if c.title.strip()][:6]
        try:
            marks, adjusted = balance_marks([c.max_marks for c in crit], unit.max_marks)
        except ValueError as e:
            raise JobError(
                f"The AI could not produce a usable rubric for {unit.label}. Retry generation, or write the criteria manually.",
                code="rubric_unbalanced", retryable=True,
            ) from e
        conf = round(gen.confidence * (0.6 + 0.4 * retr_conf) * (0.9 if adjusted else 1.0), 3)
        with session_scope() as db:
            for pos, (c, m) in enumerate(zip(crit, marks)):
                db.add(RubricCriterion(
                    rubric_version_id=version_id, question_id=unit.question_id, subquestion_id=unit.subquestion_id, position=pos,
                    title=c.title.strip()[:300], description=c.description.strip(), expected_points=c.expected_points.strip(),
                    max_marks=m, generation_confidence=conf,
                ))
    return JobResult(message=f"Rubric drafted for {total} question parts")


# ---------------------------------------------------------------------------------------------------
# Editing / versioning / approval (API side)
# ---------------------------------------------------------------------------------------------------
def _unit_key(qid, sid):
    return (qid, sid)


def build_rubric_out(db: Session, exam: Exam, version_id: uuid.UUID | None = None) -> RubricOut:
    rubric = db.scalar(select(Rubric).where(Rubric.exam_id == exam.id))
    questions = list(db.scalars(select(Question).where(Question.exam_id == exam.id).order_by(Question.position)))
    units = units_of(questions)
    if rubric is None:
        return RubricOut(exam_id=exam.id, versions=[], version=None, units=[], all_balanced=False, editable=False)
    versions = list(db.scalars(select(RubricVersion).where(RubricVersion.rubric_id == rubric.id).order_by(RubricVersion.version_number.desc())))
    shown = next((v for v in versions if v.id == version_id), None) if version_id else None
    if shown is None:
        shown = next((v for v in versions if v.status == RubricVersionStatus.DRAFT), None) or next(
            (v for v in versions if v.status == RubricVersionStatus.APPROVED), None) or (versions[0] if versions else None)
    used = dict(db.execute(select(Evaluation.rubric_version_id, func.count()).group_by(Evaluation.rubric_version_id)).all())
    v_out = [VersionOut.model_validate(v) for v in versions]
    for vo in v_out:
        vo.evaluations_using = int(used.get(vo.id, 0))
    if shown is None:
        return RubricOut(exam_id=exam.id, versions=v_out, version=None, units=[], all_balanced=False, editable=False)

    crit_by_unit: dict[tuple, list[RubricCriterion]] = {}
    for c in db.scalars(select(RubricCriterion).where(RubricCriterion.rubric_version_id == shown.id).order_by(RubricCriterion.position)):
        crit_by_unit.setdefault((c.question_id, c.subquestion_id), []).append(c)
    q_text = {q.id: q for q in questions}
    unit_out: list[UnitOut] = []
    for u in units:
        cs = crit_by_unit.get((u.question_id, u.subquestion_id), [])
        total = q2(sum((c.max_marks for c in cs), Decimal(0)))
        q = q_text[u.question_id]
        text = next((s.text for s in q.subquestions if s.id == u.subquestion_id), q.text) if u.subquestion_id else q.text
        unit_out.append(UnitOut(
            question_id=u.question_id, subquestion_id=u.subquestion_id, label=u.label, question_text=text, topic=u.topic,
            max_marks=u.max_marks, criteria_total=total, balanced=bool(cs) and total == u.max_marks,
            criteria=[CriterionOut.model_validate(c) for c in cs],
        ))
    return RubricOut(
        exam_id=exam.id, versions=v_out, version=next(v for v in v_out if v.id == shown.id), units=unit_out,
        all_balanced=bool(unit_out) and all(u.balanced for u in unit_out), editable=shown.status == RubricVersionStatus.DRAFT
    )


def replace_draft_criteria(db: Session, exam: Exam, body: RubricPut, user_id: uuid.UUID) -> RubricVersion:
    draft = draft_version(db, exam.id)
    if draft is None:
        raise Conflict("There is no draft rubric to edit. Create a new version first.", code="no_draft")
    questions = list(db.scalars(select(Question).where(Question.exam_id == exam.id)))
    valid = {(u.question_id, u.subquestion_id) for u in units_of(questions)}
    seen: set[tuple] = set()
    for u in body.units:
        key = (u.question_id, u.subquestion_id)
        if key not in valid:
            raise Unprocessable("A rubric section refers to a question that is not part of this exam.", code="unknown_question")
        if key in seen:
            raise Unprocessable("A question appears twice in the rubric.", code="duplicate_unit")
        seen.add(key)

    old = {c.id: c for c in db.scalars(select(RubricCriterion).where(RubricCriterion.rubric_version_id == draft.id))}
    db.execute(delete(RubricCriterion).where(RubricCriterion.rubric_version_id == draft.id))
    db.flush()
    for u in body.units:
        for pos, c in enumerate(u.criteria):
            prev = old.get(c.id) if c.id else None
            unchanged = prev is not None and (prev.title, prev.description or "", prev.expected_points or "", prev.max_marks) == (
                c.title.strip(), (c.description or "").strip(), (c.expected_points or "").strip(), q2(c.max_marks))
            db.add(RubricCriterion(
                id=c.id if prev is not None else uuid.uuid4(), rubric_version_id=draft.id, question_id=u.question_id, subquestion_id=u.subquestion_id,
                position=pos, title=c.title.strip(), description=(c.description or "").strip() or None,
                expected_points=(c.expected_points or "").strip() or None, max_marks=q2(c.max_marks),
                generation_confidence=prev.generation_confidence if unchanged else 1.0,  # teacher-authored = fully grounded
            ))
    draft.source = RubricSource.TEACHER if draft.source == RubricSource.AI else draft.source
    db.flush()
    return draft


def approve_draft(db: Session, exam: Exam, user_id: uuid.UUID) -> RubricVersion:
    draft = draft_version(db, exam.id)
    if draft is None:
        raise NotFound("There is no draft rubric to approve.")
    out = build_rubric_out(db, exam, draft.id)
    bad = [u for u in out.units if not u.balanced]
    if bad:
        raise Unprocessable(
            "Every question part needs criteria that add up exactly to its maximum marks before the rubric can be approved.",
            code="rubric_unbalanced",
            details=[{"label": u.label, "max_marks": float(u.max_marks), "criteria_total": float(u.criteria_total)} for u in bad],
        )
    now = datetime.now(timezone.utc)
    prev = approved_version(db, exam.id)
    if prev is not None:
        prev.status = RubricVersionStatus.SUPERSEDED
        db.flush()
    draft.status = RubricVersionStatus.APPROVED
    draft.approved_by, draft.approved_at = user_id, now
    db.flush()
    return draft


def new_draft_from_latest(db: Session, exam: Exam, user_id: uuid.UUID) -> RubricVersion:
    if draft_version(db, exam.id) is not None:
        raise Conflict("A draft rubric already exists; edit or approve it first.", code="draft_exists")
    rubric = get_or_create_rubric(db, exam.id)
    src = approved_version(db, exam.id) or db.scalar(
        select(RubricVersion).where(RubricVersion.rubric_id == rubric.id).order_by(RubricVersion.version_number.desc()).limit(1)
    )
    draft = RubricVersion(rubric_id=rubric.id, version_number=_next_version_number(db, rubric.id), status=RubricVersionStatus.DRAFT,
                          source=RubricSource.TEACHER, created_by=user_id,
                          notes=f"copied from v{src.version_number}" if src else "blank draft")
    db.add(draft)
    db.flush()
    if src is not None:
        for c in db.scalars(select(RubricCriterion).where(RubricCriterion.rubric_version_id == src.id)):
            db.add(RubricCriterion(
                rubric_version_id=draft.id, question_id=c.question_id, subquestion_id=c.subquestion_id, position=c.position,
                title=c.title, description=c.description, expected_points=c.expected_points, max_marks=c.max_marks,
                generation_confidence=c.generation_confidence,
            ))
    db.flush()
    return draft
