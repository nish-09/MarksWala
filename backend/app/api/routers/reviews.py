from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import DB, CurrentUser, client_ip
from app.core.errors import Conflict, Unprocessable
from app.models import AnswerSheet, Evaluation, TeacherOverride
from app.models.enums import CourseRole, JobKind, SheetStatus
from app.schemas.jobs import JobOut
from app.schemas.review import QueueOut, ReviewDetailOut
from app.services import access, jobs, review_service, review_views, rubric_service
from app.services.review_service import AcceptIn, OverrideIn

router = APIRouter(tags=["review"])


class ReEvaluateIn(BaseModel):
    use_corrected_text: bool = True
    discard_overrides: bool = False


class EvaluateIn(BaseModel):
    use_corrected_text: bool = True


@router.get("/exams/{exam_id}/review", response_model=QueueOut)
def review_queue(exam_id: uuid.UUID, db: DB, user: CurrentUser, status: str | None = Query(default="PENDING"),
                 mandatory_only: bool = False, sheet_id: uuid.UUID | None = None):
    exam = access.get_exam(db, user, exam_id)
    return review_views.review_queue(db, exam, status=status if status and status != "ALL" else None, mandatory_only=mandatory_only, sheet_id=sheet_id)


@router.get("/answers/{answer_id}/review", response_model=ReviewDetailOut)
def review_detail(answer_id: uuid.UUID, db: DB, user: CurrentUser):
    answer, sheet, exam = access.get_answer(db, user, answer_id)
    return review_views.review_detail(db, sheet, exam, answer)


@router.post("/evaluations/{evaluation_id}/accept", response_model=ReviewDetailOut)
def accept(evaluation_id: uuid.UUID, body: AcceptIn, request: Request, db: DB, user: CurrentUser):
    ev, answer, sheet, exam = access.get_evaluation(db, user, evaluation_id, CourseRole.INSTRUCTOR)
    review_service.accept(db, user, ev, exam, body.notes, client_ip(request))
    db.commit()
    return review_views.review_detail(db, sheet, exam, answer)


@router.post("/evaluations/{evaluation_id}/override", response_model=ReviewDetailOut)
def override(evaluation_id: uuid.UUID, body: OverrideIn, request: Request, db: DB, user: CurrentUser):
    ev, answer, sheet, exam = access.get_evaluation(db, user, evaluation_id, CourseRole.INSTRUCTOR)
    review_service.override(db, user, ev, exam, body, client_ip(request))
    db.commit()
    return review_views.review_detail(db, sheet, exam, answer)


@router.post("/answers/{answer_id}/re-evaluate", response_model=JobOut, status_code=202)
def re_evaluate(answer_id: uuid.UUID, body: ReEvaluateIn, request: Request, db: DB, user: CurrentUser):
    """Run the AI evaluation again for one answer (e.g. after an OCR correction). Teacher overrides are never lost silently."""
    answer, sheet, exam = access.get_answer(db, user, answer_id, CourseRole.INSTRUCTOR)
    if rubric_service.approved_version(db, exam.id) is None:
        raise Unprocessable("Approve a rubric before evaluating.", code="no_approved_rubric")
    current = db.scalar(select(Evaluation).where(Evaluation.answer_id == answer.id, Evaluation.is_current))
    if current is not None:
        has_overrides = db.scalar(select(TeacherOverride.id).where(TeacherOverride.evaluation_id == current.id, TeacherOverride.is_active).limit(1))
        if has_overrides and not body.discard_overrides:
            raise Conflict("You have overridden marks for this answer. Re-evaluating would replace them with a new AI result "
                           "(your overrides stay in the history). Confirm to continue.", code="has_overrides")
    job = jobs.create_job(db, kind=JobKind.EVALUATE_ANSWER_SHEET, entity_type="answer_sheet", entity_id=sheet.id, course_id=exam.course_id, exam_id=exam.id,
                          user_id=user.id, payload={"answer_ids": [str(answer.id)], "use_corrected_text": body.use_corrected_text})
    from app.services import audit

    audit.record(db, actor_id=user.id, action="answer.re_evaluate", entity_type="answer", entity_id=answer.id, course_id=exam.course_id, exam_id=exam.id,
                 after={"use_corrected_text": body.use_corrected_text, "discarded_overrides": bool(current and body.discard_overrides)}, ip=client_ip(request))
    db.commit()
    jobs.dispatch(job.id)
    return JobOut.model_validate(job)


@router.post("/answer-sheets/{sheet_id}/evaluate", response_model=JobOut, status_code=202)
def evaluate_sheet(sheet_id: uuid.UUID, body: EvaluateIn, db: DB, user: CurrentUser):
    sheet, exam = access.get_sheet(db, user, sheet_id, CourseRole.INSTRUCTOR)
    if sheet.status in (SheetStatus.UPLOADED, SheetStatus.PROCESSING, SheetStatus.FAILED):
        raise Conflict("Wait until the sheet has been read (or reprocess it) before evaluating.", code="sheet_not_ready")
    job = jobs.enqueue(db, kind=JobKind.EVALUATE_ANSWER_SHEET, entity_type="answer_sheet", entity_id=sheet.id, course_id=exam.course_id, exam_id=exam.id,
                       user_id=user.id, payload={"use_corrected_text": body.use_corrected_text})
    return JobOut.model_validate(job)


@router.post("/exams/{exam_id}/evaluate", response_model=list[JobOut], status_code=202)
def evaluate_exam(exam_id: uuid.UUID, body: EvaluateIn, db: DB, user: CurrentUser):
    """Evaluate every processed sheet that still lacks an evaluation with the current rubric."""
    exam = access.get_exam(db, user, exam_id, CourseRole.INSTRUCTOR)
    if rubric_service.approved_version(db, exam.id) is None:
        raise Unprocessable("Approve a rubric before evaluating answers.", code="no_approved_rubric")
    sheets = db.scalars(select(AnswerSheet).where(AnswerSheet.exam_id == exam.id, AnswerSheet.deleted_at.is_(None),
                                                  AnswerSheet.status.in_([SheetStatus.PROCESSED, SheetStatus.EVALUATED]))).all()
    out: list[JobOut] = []
    for s in sheets:
        try:
            j = jobs.enqueue(db, kind=JobKind.EVALUATE_ANSWER_SHEET, entity_type="answer_sheet", entity_id=s.id, course_id=exam.course_id, exam_id=exam.id,
                             user_id=user.id, payload={"use_corrected_text": body.use_corrected_text})
            out.append(JobOut.model_validate(j))
        except Conflict:
            continue  # already running for that sheet
    return out
