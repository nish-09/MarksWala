from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.deps import DB, CurrentUser
from app.models import AnswerSheet, Course, Evaluation, Exam, ProcessingJob, Resource, TeacherReview
from app.models.enums import JobStatus, ResourceStatus, ReviewStatus, SheetStatus
from app.services import access

router = APIRouter(tags=["dashboard"])


class DashboardOut(BaseModel):
    courses: int
    exams: int
    resources_ready: int
    resources_total: int
    sheets_total: int
    sheets_evaluated: int
    reviews_mandatory: int
    reviews_recommended: int
    jobs_active: int
    jobs_failed_recent: int


@router.get("/dashboard", response_model=DashboardOut)
def dashboard(db: DB, user: CurrentUser):
    """Aggregates for the teacher's home screen, computed in SQL over the courses they belong to."""
    cids = access.course_ids_for(db, user)

    def count(stmt) -> int:
        return db.scalar(stmt) or 0

    exam_ids = select(Exam.id).where(Exam.course_id.in_(cids), Exam.deleted_at.is_(None))
    live_sheets = select(AnswerSheet.id).where(AnswerSheet.exam_id.in_(exam_ids), AnswerSheet.deleted_at.is_(None))
    pending = (
        select(func.count()).select_from(TeacherReview)
        .join(Evaluation, Evaluation.id == TeacherReview.evaluation_id)
        .where(TeacherReview.answer_sheet_id.in_(live_sheets), TeacherReview.status == ReviewStatus.PENDING, Evaluation.is_current)
    )
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    return DashboardOut(
        courses=count(select(func.count()).select_from(Course).where(Course.id.in_(cids), Course.deleted_at.is_(None))),
        exams=count(select(func.count()).select_from(Exam).where(Exam.course_id.in_(cids), Exam.deleted_at.is_(None))),
        resources_total=count(select(func.count()).select_from(Resource).where(Resource.course_id.in_(cids), Resource.deleted_at.is_(None))),
        resources_ready=count(select(func.count()).select_from(Resource).where(Resource.course_id.in_(cids), Resource.deleted_at.is_(None), Resource.status == ResourceStatus.COMPLETED)),
        sheets_total=count(select(func.count()).select_from(AnswerSheet).where(AnswerSheet.exam_id.in_(exam_ids), AnswerSheet.deleted_at.is_(None))),
        sheets_evaluated=count(select(func.count()).select_from(AnswerSheet).where(AnswerSheet.exam_id.in_(exam_ids), AnswerSheet.deleted_at.is_(None), AnswerSheet.status == SheetStatus.EVALUATED)),
        reviews_mandatory=count(pending.where(TeacherReview.mandatory.is_(True))),
        reviews_recommended=count(pending.where(TeacherReview.mandatory.is_(False))),
        jobs_active=count(select(func.count()).select_from(ProcessingJob).where(ProcessingJob.course_id.in_(cids), ProcessingJob.status.in_([JobStatus.QUEUED, JobStatus.PROCESSING]))),
        jobs_failed_recent=count(select(func.count()).select_from(ProcessingJob).where(ProcessingJob.course_id.in_(cids), ProcessingJob.status == JobStatus.FAILED, ProcessingJob.queued_at > week_ago)),
    )
