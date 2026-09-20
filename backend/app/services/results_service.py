"""Results: derived exclusively from persisted evaluations + teacher overrides (never from the browser or the LLM).

effective criterion score = active teacher override's final_score, else the AI criterion score.
unit score = sum(effective criterion scores); question = sum(units); exam = standalone questions + the best
`choice_count` alternatives of each internal-choice group.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import (
    Answer,
    AnswerSheet,
    ConfidenceFlag,
    Evaluation,
    EvaluationCriterion,
    Exam,
    Question,
    StudentResult,
    TeacherOverride,
    TeacherReview,
    TopicResult,
)
from app.models.enums import FlagSeverity, ResultStatus, ReviewStatus
from app.services import exam_state
from app.services.marks import Unit, exam_total, q2, units_of

UNCLASSIFIED = "Unclassified"


@dataclass
class UnitScore:
    unit: Unit
    answer_id: uuid.UUID | None
    evaluation_id: uuid.UUID | None
    ai_score: Decimal | None
    final_score: Decimal
    overridden: bool
    evaluated: bool
    counted: bool = True


@dataclass
class SheetResult:
    sheet_id: uuid.UUID
    exam_id: uuid.UUID
    student_id: uuid.UUID | None
    units: list[UnitScore]
    question_totals: dict[int, tuple[Decimal, Decimal, bool]]  # number -> (score, max, counted)
    total: Decimal
    max_total: Decimal
    percentage: Decimal
    passed: bool | None
    status: ResultStatus
    pending_reviews: int
    unevaluated: int
    sheet_flags_open: int
    topics: dict[str, tuple[Decimal, Decimal]] = field(default_factory=dict)


def effective_scores(db: Session, evaluation_ids: list[uuid.UUID]) -> dict[uuid.UUID, tuple[Decimal, Decimal, bool]]:
    """evaluation_id -> (ai_total, final_total, any_override)."""
    if not evaluation_ids:
        return {}
    crits = db.scalars(select(EvaluationCriterion).where(EvaluationCriterion.evaluation_id.in_(evaluation_ids))).all()
    overrides = {o.evaluation_criterion_id: o for o in db.scalars(
        select(TeacherOverride).where(TeacherOverride.evaluation_id.in_(evaluation_ids), TeacherOverride.is_active))}
    out: dict[uuid.UUID, list[Decimal | bool]] = {}
    for c in crits:
        ai, fin, ov = out.setdefault(c.evaluation_id, [Decimal(0), Decimal(0), False])
        o = overrides.get(c.id)
        out[c.evaluation_id] = [ai + c.score, fin + (o.final_score if o else c.score), ov or o is not None]
    return {k: (q2(v[0]), q2(v[1]), bool(v[2])) for k, v in out.items()}


def compute_sheet_result(db: Session, sheet_id: uuid.UUID) -> SheetResult:
    sheet = db.get(AnswerSheet, sheet_id)
    exam = db.get(Exam, sheet.exam_id)
    questions = list(db.scalars(select(Question).where(Question.exam_id == exam.id).order_by(Question.position)))
    units = units_of(questions)
    answers = {(a.question_id, a.subquestion_id): a for a in db.scalars(select(Answer).where(Answer.answer_sheet_id == sheet_id))}
    evals = {e.answer_id: e for e in db.scalars(
        select(Evaluation).where(Evaluation.answer_id.in_([a.id for a in answers.values()]), Evaluation.is_current))} if answers else {}
    eff = effective_scores(db, [e.id for e in evals.values()])

    scores: list[UnitScore] = []
    for u in units:
        a = answers.get((u.question_id, u.subquestion_id))
        e = evals.get(a.id) if a else None
        if e is not None and e.id in eff:
            ai, fin, ov = eff[e.id]
            scores.append(UnitScore(u, a.id, e.id, ai, fin, ov, True))
        else:
            scores.append(UnitScore(u, a.id if a else None, None, None, Decimal("0.00"), False, False))

    # per-question totals and which alternatives count
    by_q: dict[int, list[UnitScore]] = {}
    for s in scores:
        by_q.setdefault(s.unit.number, []).append(s)
    qtot = {n: (q2(sum((s.final_score for s in ss), Decimal(0))), q2(sum((s.unit.max_marks for s in ss), Decimal(0)))) for n, ss in by_q.items()}
    counted: dict[int, bool] = {n: True for n in by_q}
    groups: dict[str, list[Question]] = {}
    for q in questions:
        if q.choice_group:
            groups.setdefault(q.choice_group, []).append(q)
    for members in groups.values():
        n = max(m.choice_count for m in members)
        ranked = sorted(members, key=lambda m: (-qtot[m.number][0], m.number))
        keep = {m.number for m in ranked[:n]}
        for m in members:
            counted[m.number] = m.number in keep
    for s in scores:
        s.counted = counted[s.unit.number]

    total = q2(sum((qtot[n][0] for n in qtot if counted[n]), Decimal(0)))
    max_total = exam_total(questions)
    pct = q2(total / max_total * 100) if max_total > 0 else Decimal("0.00")
    passed = pct >= exam_state.pass_percentage(exam)

    topics: dict[str, tuple[Decimal, Decimal]] = {}
    for s in scores:
        if not s.counted:
            continue
        t = (s.unit.topic or UNCLASSIFIED)
        sc, mx = topics.get(t, (Decimal(0), Decimal(0)))
        topics[t] = (sc + s.final_score, mx + s.unit.max_marks)

    ans_ids = [a.id for a in answers.values()]
    pending = 0
    if ans_ids:
        ev_ids = [e.id for e in evals.values()]
        pending = len(db.scalars(select(TeacherReview.id).where(TeacherReview.evaluation_id.in_(ev_ids), TeacherReview.status == ReviewStatus.PENDING)).all()) if ev_ids else 0
    sheet_flags = len(db.scalars(select(ConfidenceFlag.id).where(
        ConfidenceFlag.answer_sheet_id == sheet_id, ConfidenceFlag.answer_id.is_(None), ConfidenceFlag.evaluation_id.is_(None),
        ConfidenceFlag.severity == FlagSeverity.MANDATORY, ConfidenceFlag.resolved_at.is_(None))).all())
    unevaluated = sum(1 for s in scores if not s.evaluated)
    final = unevaluated == 0 and pending == 0 and sheet_flags == 0
    return SheetResult(
        sheet_id=sheet_id, exam_id=exam.id, student_id=sheet.student_id, units=scores,
        question_totals={n: (qtot[n][0], qtot[n][1], counted[n]) for n in qtot},
        total=total, max_total=max_total, percentage=pct, passed=passed,
        status=ResultStatus.FINAL if final else ResultStatus.PROVISIONAL,
        pending_reviews=pending, unevaluated=unevaluated, sheet_flags_open=sheet_flags,
        topics={t: (q2(sc), q2(mx)) for t, (sc, mx) in topics.items()},
    )


def recompute_sheet(db: Session, sheet_id: uuid.UUID) -> StudentResult:
    """Recompute and persist the sheet's totals and topic breakdown (call inside the mutating transaction)."""
    r = compute_sheet_result(db, sheet_id)
    row = db.scalar(select(StudentResult).where(StudentResult.answer_sheet_id == sheet_id))
    if row is None:
        row = StudentResult(exam_id=r.exam_id, answer_sheet_id=sheet_id, student_id=r.student_id, total=r.total, max_total=r.max_total,
                            percentage=r.percentage, status=r.status)
        db.add(row)
    row.student_id, row.total, row.max_total, row.percentage = r.student_id, r.total, r.max_total, r.percentage
    row.passed, row.status, row.pending_reviews = r.passed, r.status, r.pending_reviews + r.sheet_flags_open
    row.computed_at = datetime.now(timezone.utc)
    db.execute(delete(TopicResult).where(TopicResult.answer_sheet_id == sheet_id))
    for topic, (sc, mx) in r.topics.items():
        db.add(TopicResult(exam_id=r.exam_id, answer_sheet_id=sheet_id, student_id=r.student_id, topic=topic[:200], score=sc, max_score=mx,
                           percentage=q2(sc / mx * 100) if mx > 0 else Decimal("0.00")))
    db.flush()
    return row
