"""Read models for the review queue and the answer-review workspace (the full 'why this mark?' trace)."""
from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Answer,
    AnswerSheet,
    ConfidenceFlag,
    Evaluation,
    EvaluationCriterion,
    EvaluationSource,
    Exam,
    Question,
    RubricCriterion,
    RubricVersion,
    Student,
    TeacherOverride,
    TeacherReview,
    User,
)
from app.models.enums import ReviewStatus
from app.schemas.review import (
    ConfidenceOut,
    CriterionEvalOut,
    EvalHistoryOut,
    EvaluationDetailOut,
    OverrideOut,
    QueueItemOut,
    QueueOut,
    ReviewDetailOut,
    ReviewStateOut,
    SourceOut,
)
from app.schemas.sheets import FlagOut, StudentOut
from app.services import confidence as conf
from app.services import exam_state, results_service, rubric_service, sheet_views
from app.services.marks import units_of


def band_for(exam: Exam, overall: float | None) -> str | None:
    if overall is None:
        return None
    hi, lo = exam_state.thresholds(exam)
    return conf.band(overall, hi, lo)


def _stale(answer: Answer, ev: Evaluation) -> bool:
    """True when the answer's effective text (corrected if present) differs from the text this evaluation used."""
    return answer.effective_text != ev.answer_text_snapshot


def _override_out(o: TeacherOverride, names: dict[uuid.UUID, str]) -> OverrideOut:
    return OverrideOut(id=o.id, ai_score=o.ai_score, teacher_score=o.teacher_score, final_score=o.final_score, reason=o.reason,
                       teacher_id=o.teacher_id, teacher_name=names.get(o.teacher_id), is_active=o.is_active, created_at=o.created_at)


def evaluation_detail(db: Session, exam: Exam, answer: Answer, ev: Evaluation) -> EvaluationDetailOut:
    crits = list(db.scalars(select(EvaluationCriterion).where(EvaluationCriterion.evaluation_id == ev.id).order_by(EvaluationCriterion.position)))
    rc = {c.id: c for c in db.scalars(select(RubricCriterion).where(RubricCriterion.id.in_([c.rubric_criterion_id for c in crits])))}
    overrides = list(db.scalars(select(TeacherOverride).where(TeacherOverride.evaluation_id == ev.id).order_by(TeacherOverride.created_at)))
    names = {u.id: u.full_name for u in db.scalars(select(User).where(User.id.in_({o.teacher_id for o in overrides})))} if overrides else {}
    by_crit: dict[uuid.UUID, list[TeacherOverride]] = {}
    for o in overrides:
        by_crit.setdefault(o.evaluation_criterion_id, []).append(o)

    crit_out: list[CriterionEvalOut] = []
    final_total = Decimal(0)
    for c in crits:
        hist = by_crit.get(c.id, [])
        act = next((o for o in hist if o.is_active), None)
        final = act.final_score if act else c.score
        final_total += final
        r = rc[c.rubric_criterion_id]
        crit_out.append(CriterionEvalOut(
            id=c.id, rubric_criterion_id=c.rubric_criterion_id, position=c.position, title=r.title, description=r.description, expected_points=r.expected_points,
            max_score=c.max_score, ai_score=c.score, final_score=final, satisfied=c.satisfied, partial=c.partial, evidence=c.evidence,
            missing_points=c.missing_points, feedback=c.feedback, confidence=c.confidence,
            override=_override_out(act, names) if act else None, override_history=[_override_out(o, names) for o in hist],
        ))
    sources = db.scalars(select(EvaluationSource).where(EvaluationSource.evaluation_id == ev.id).order_by(EvaluationSource.rank))
    flags = db.scalars(select(ConfidenceFlag).where(ConfidenceFlag.evaluation_id == ev.id).order_by(ConfidenceFlag.created_at))
    rv = db.scalar(select(TeacherReview).where(TeacherReview.evaluation_id == ev.id))
    reviewer = db.get(User, rv.reviewer_id).full_name if rv and rv.reviewer_id else None
    ver = db.get(RubricVersion, ev.rubric_version_id)
    return EvaluationDetailOut(
        id=ev.id, attempt=ev.attempt, status=ev.status, text_source=ev.text_source, answer_text_used=ev.answer_text_snapshot, provider=ev.provider,
        model=ev.model, prompt_version=ev.prompt_version, rubric_version_id=ev.rubric_version_id, rubric_version_number=ver.version_number,
        ai_total=ev.ai_total, final_total=q2_dec(final_total), max_total=ev.max_total, overall_feedback=ev.overall_feedback, error_message=ev.error_message,
        confidence=ConfidenceOut(ocr=ev.ocr_confidence, mapping=ev.mapping_confidence, retrieval=ev.retrieval_confidence, rubric=ev.rubric_confidence,
                                 evaluation=ev.evaluation_confidence, overall=ev.overall_confidence, band=band_for(exam, ev.overall_confidence) or "mandatory_review"),
        criteria=crit_out,
        sources=[SourceOut(chunk_id=s.chunk_id, resource_id=s.resource_id, resource_title=s.resource_title, page_number=s.page_number,
                           slide_number=s.slide_number, section=s.section, rank=s.rank, score=s.score, text=s.text_snapshot) for s in sources],
        flags=[FlagOut.model_validate(f) for f in flags],
        review=ReviewStateOut(id=rv.id if rv else None, status=rv.status if rv else None, mandatory=rv.mandatory if rv else False,
                              decision=rv.decision if rv else None, reviewer_name=reviewer, notes=rv.notes if rv else None,
                              resolved_at=rv.resolved_at if rv else None),
        stale=_stale(answer, ev), created_at=ev.created_at,
    )


def q2_dec(v: Decimal) -> Decimal:
    return v.quantize(Decimal("0.01"))


def review_detail(db: Session, sheet: AnswerSheet, exam: Exam, answer: Answer) -> ReviewDetailOut:
    detail = sheet_views.sheet_detail(db, sheet)
    ans_out = next(a for a in detail.answers if a.id == answer.id)
    q = db.get(Question, answer.question_id)
    sub = next((s for s in q.subquestions if s.id == answer.subquestion_id), None) if answer.subquestion_id else None
    ev = db.scalar(select(Evaluation).where(Evaluation.answer_id == answer.id, Evaluation.is_current))
    hist = list(db.scalars(select(Evaluation).where(Evaluation.answer_id == answer.id).order_by(Evaluation.attempt.desc())))
    student = db.get(Student, sheet.student_id) if sheet.student_id else None
    # neighbours in the queue order (by question position within the sheet) for next/previous navigation
    units = units_of(list(db.scalars(select(Question).where(Question.exam_id == exam.id).order_by(Question.position))))
    by_key = {(a.question_id, a.subquestion_id): a.id for a in db.scalars(select(Answer).where(Answer.answer_sheet_id == sheet.id))}  # one query, not one per part
    order = [by_key[(u.question_id, u.subquestion_id)] for u in units if (u.question_id, u.subquestion_id) in by_key]
    i = order.index(answer.id)
    pages = [p for p in detail.pages if p.page_number in ans_out.pages] or detail.pages
    return ReviewDetailOut(
        exam_id=exam.id, exam_title=exam.title, sheet_id=sheet.id, student=StudentOut.model_validate(student) if student else None,
        question_label=ans_out.label, question_text=sub.text if sub else q.text, question_stem=q.text if sub else None,
        max_marks=ans_out.max_marks, answer=ans_out, pages=pages,
        evaluation=evaluation_detail(db, exam, answer, ev) if ev else None,
        history=[EvalHistoryOut(id=h.id, attempt=h.attempt, is_current=h.is_current, ai_total=h.ai_total, text_source=h.text_source, model=h.model, created_at=h.created_at) for h in hist],
        can_evaluate=rubric_service.approved_version(db, exam.id) is not None,
        next_answer_id=order[i + 1] if i + 1 < len(order) else None, prev_answer_id=order[i - 1] if i > 0 else None,
    )


def review_queue(db: Session, exam: Exam, *, status: str | None, mandatory_only: bool, sheet_id: uuid.UUID | None) -> QueueOut:
    hi, lo = exam_state.thresholds(exam)
    sheets = {s.id: s for s in db.scalars(select(AnswerSheet).where(AnswerSheet.exam_id == exam.id, AnswerSheet.deleted_at.is_(None)))}
    students = {s.id: s for s in db.scalars(select(Student).where(Student.id.in_([s.student_id for s in sheets.values() if s.student_id])))} if sheets else {}
    questions = list(db.scalars(select(Question).where(Question.exam_id == exam.id)))
    unit_by = {(u.question_id, u.subquestion_id): u for u in units_of(questions)}

    items: list[QueueItemOut] = []
    reviews = db.execute(
        select(TeacherReview, Evaluation, Answer)
        .join(Evaluation, Evaluation.id == TeacherReview.evaluation_id).join(Answer, Answer.id == Evaluation.answer_id)
        .where(TeacherReview.answer_sheet_id.in_(list(sheets)), Evaluation.is_current)
    ).all() if sheets else []
    for rv, ev, ans in reviews:
        sh = sheets[rv.answer_sheet_id]
        flags = list(db.scalars(select(ConfidenceFlag).where(ConfidenceFlag.evaluation_id == ev.id)))
        _, final, _ = results_service.effective_scores(db, [ev.id]).get(ev.id, (ev.ai_total, ev.ai_total, False))
        u = unit_by.get((ans.question_id, ans.subquestion_id))
        st = students.get(sh.student_id) if sh.student_id else None
        items.append(QueueItemOut(
            kind="EVALUATION", review_id=rv.id, evaluation_id=ev.id, answer_id=ans.id, sheet_id=sh.id, student=StudentOut.model_validate(st) if st else None,
            sheet_filename=sh.original_filename, question_label=u.label if u else None, max_marks=ev.max_total, ai_total=ev.ai_total, final_total=final,
            overall_confidence=ev.overall_confidence, band=conf.band(ev.overall_confidence or 0, hi, lo), mandatory=rv.mandatory, status=rv.status,
            decision=rv.decision, stale=_stale(ans, ev), reasons=[f.detail for f in flags], flag_kinds=[f.kind for f in flags], severities=[f.severity for f in flags],
        ))
    # sheet-level problems (student not identified, unassigned segments)
    sflags = db.scalars(select(ConfidenceFlag).where(
        ConfidenceFlag.answer_sheet_id.in_(list(sheets)), ConfidenceFlag.answer_id.is_(None), ConfidenceFlag.evaluation_id.is_(None))).all() if sheets else []
    for f in sflags:
        sh = sheets[f.answer_sheet_id]
        st = students.get(sh.student_id) if sh.student_id else None
        items.append(QueueItemOut(
            kind="SHEET", review_id=None, evaluation_id=None, answer_id=None, sheet_id=sh.id, student=StudentOut.model_validate(st) if st else None,
            sheet_filename=sh.original_filename, question_label=None, max_marks=None, ai_total=None, final_total=None, overall_confidence=f.value,
            band=None, mandatory=f.severity.value == "MANDATORY", status=ReviewStatus.RESOLVED if f.resolved_at else ReviewStatus.PENDING, decision=None,
            stale=False, reasons=[f.detail], flag_kinds=[f.kind], severities=[f.severity],
        ))
    pend_m = sum(1 for i in items if i.status == ReviewStatus.PENDING and i.mandatory)
    pend_r = sum(1 for i in items if i.status == ReviewStatus.PENDING and not i.mandatory)
    resolved = sum(1 for i in items if i.status == ReviewStatus.RESOLVED)
    if status:
        items = [i for i in items if (i.status.value if i.status else None) == status.upper()]
    if mandatory_only:
        items = [i for i in items if i.mandatory]
    if sheet_id:
        items = [i for i in items if i.sheet_id == sheet_id]
    items.sort(key=lambda i: (0 if i.mandatory else 1, i.overall_confidence if i.overall_confidence is not None else 0, i.sheet_filename))
    return QueueOut(items=items, pending_mandatory=pend_m, pending_recommended=pend_r, resolved=resolved)
