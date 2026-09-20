"""Teacher decisions on AI evaluations. AI results are never edited: overrides are appended, reviews resolved."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.errors import Conflict, Unprocessable
from app.models import ConfidenceFlag, Evaluation, EvaluationCriterion, Exam, TeacherOverride, TeacherReview, User
from app.models.enums import ReviewDecision, ReviewStatus
from app.services import audit, results_service
from app.services.marks import q2


class OverrideItem(BaseModel):
    evaluation_criterion_id: uuid.UUID
    teacher_score: float = Field(ge=0, le=1000)
    reason: str = Field(min_length=3, max_length=2000)


class OverrideIn(BaseModel):
    overrides: list[OverrideItem] = Field(default=[], max_length=50)
    revert_criterion_ids: list[uuid.UUID] = Field(default=[], max_length=50)
    notes: str | None = Field(default=None, max_length=4000)


class AcceptIn(BaseModel):
    notes: str | None = Field(default=None, max_length=4000)


def _review(db: Session, ev: Evaluation) -> TeacherReview:
    rv = db.scalar(select(TeacherReview).where(TeacherReview.evaluation_id == ev.id))
    if rv is None:
        from app.models import Answer

        sheet_id = db.get(Answer, ev.answer_id).answer_sheet_id
        rv = TeacherReview(evaluation_id=ev.id, answer_sheet_id=sheet_id, status=ReviewStatus.PENDING, mandatory=False)
        db.add(rv)
        db.flush()
    return rv


def _resolve_flags(db: Session, ev: Evaluation) -> None:
    db.execute(update(ConfidenceFlag).where(ConfidenceFlag.evaluation_id == ev.id, ConfidenceFlag.resolved_at.is_(None)).values(resolved_at=datetime.now(timezone.utc)))


def _require_current(ev: Evaluation) -> None:
    if not ev.is_current:
        raise Conflict("A newer evaluation exists for this answer; open the latest one.", code="evaluation_superseded")


def _totals(db: Session, ev: Evaluation) -> tuple[Decimal, Decimal]:
    ai, fin, _ = results_service.effective_scores(db, [ev.id]).get(ev.id, (Decimal(0), Decimal(0), False))
    return ai, fin


def accept(db: Session, user: User, ev: Evaluation, exam: Exam, notes: str | None, ip: str | None) -> TeacherReview:
    _require_current(ev)
    rv = _review(db, ev)
    _, fin = _totals(db, ev)
    rv.status, rv.decision, rv.reviewer_id, rv.notes = ReviewStatus.RESOLVED, ReviewDecision.ACCEPTED, user.id, notes
    rv.resolved_at = datetime.now(timezone.utc)
    _resolve_flags(db, ev)
    audit.record(db, actor_id=user.id, action="evaluation.accept", entity_type="evaluation", entity_id=ev.id, course_id=exam.course_id, exam_id=exam.id,
                 after={"final_total": float(fin), "ai_total": float(ev.ai_total), "notes": notes}, ip=ip)
    db.flush()
    results_service.recompute_sheet(db, rv.answer_sheet_id)
    return rv


def override(db: Session, user: User, ev: Evaluation, exam: Exam, body: OverrideIn, ip: str | None) -> TeacherReview:
    _require_current(ev)
    if not body.overrides and not body.revert_criterion_ids:
        raise Unprocessable("Nothing to change: give at least one criterion score.", code="empty_override")
    crits = {c.id: c for c in db.scalars(select(EvaluationCriterion).where(EvaluationCriterion.evaluation_id == ev.id))}
    active = {o.evaluation_criterion_id: o for o in db.scalars(select(TeacherOverride).where(TeacherOverride.evaluation_id == ev.id, TeacherOverride.is_active))}
    _, before_total = _totals(db, ev)
    seen: set[uuid.UUID] = set()

    for cid in body.revert_criterion_ids:
        if cid not in crits:
            raise Unprocessable("A criterion does not belong to this evaluation.", code="unknown_criterion")
        if cid in active:
            active[cid].is_active = False  # history row is kept; only the flag flips
            seen.add(cid)
    db.flush()
    for it in body.overrides:
        c = crits.get(it.evaluation_criterion_id)
        if c is None:
            raise Unprocessable("A criterion does not belong to this evaluation.", code="unknown_criterion")
        if it.evaluation_criterion_id in seen:
            raise Unprocessable("A criterion appears twice in one request.", code="duplicate_criterion")
        seen.add(it.evaluation_criterion_id)
        score = q2(it.teacher_score)
        if score > c.max_score:
            raise Unprocessable(f"Score {score} exceeds the criterion maximum of {c.max_score}.", code="score_above_max")
        current_eff = active[c.id].final_score if c.id in active else c.score
        if score == current_eff:
            raise Unprocessable("The score is unchanged for one of the criteria.", code="unchanged")
        if c.id in active:
            active[c.id].is_active = False
            db.flush()
        db.add(TeacherOverride(
            evaluation_id=ev.id, evaluation_criterion_id=c.id, ai_score=c.score, teacher_score=score, final_score=score,
            reason=it.reason.strip(), teacher_id=user.id, is_active=True,
        ))
    db.flush()
    _, after_total = _totals(db, ev)
    rv = _review(db, ev)
    rv.status, rv.decision, rv.reviewer_id, rv.notes = ReviewStatus.RESOLVED, ReviewDecision.OVERRIDDEN, user.id, body.notes
    rv.resolved_at = datetime.now(timezone.utc)
    _resolve_flags(db, ev)
    audit.record(db, actor_id=user.id, action="evaluation.override", entity_type="evaluation", entity_id=ev.id, course_id=exam.course_id, exam_id=exam.id,
                 before={"final_total": float(before_total)}, after={"final_total": float(after_total), "ai_total": float(ev.ai_total),
                 "changes": [{"criterion": str(i.evaluation_criterion_id), "score": i.teacher_score, "reason": i.reason} for i in body.overrides],
                 "reverted": [str(x) for x in body.revert_criterion_ids]}, ip=ip)
    db.flush()
    results_service.recompute_sheet(db, rv.answer_sheet_id)
    return rv
