from __future__ import annotations

import uuid

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.deps import DB, CurrentUser
from app.core.errors import NotFound
from app.models import AnswerSheet, Exam, ProcessingJob, Resource
from app.models.enums import CourseRole, JobStatus
from app.schemas.jobs import JobOut
from app.services import access, jobs

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _labels(db, rows: list[ProcessingJob]) -> dict[uuid.UUID, str]:
    labels: dict[uuid.UUID, str] = {}
    by_type: dict[str, list[uuid.UUID]] = {}
    for j in rows:
        by_type.setdefault(j.entity_type, []).append(j.entity_id)
    if ids := by_type.get("resource"):
        labels.update({i: t for i, t in db.execute(select(Resource.id, Resource.title).where(Resource.id.in_(ids)))})
    if ids := by_type.get("exam"):
        labels.update({i: t for i, t in db.execute(select(Exam.id, Exam.title).where(Exam.id.in_(ids)))})
    if ids := by_type.get("answer_sheet"):
        labels.update({i: t for i, t in db.execute(select(AnswerSheet.id, AnswerSheet.original_filename).where(AnswerSheet.id.in_(ids)))})
    return labels


def _out(db, rows: list[ProcessingJob]) -> list[JobOut]:
    labels = _labels(db, rows)
    out = []
    for j in rows:
        o = JobOut.model_validate(j)
        o.entity_label = labels.get(j.entity_id)
        out.append(o)
    return out


@router.get("", response_model=list[JobOut])
def list_jobs(
    db: DB,
    user: CurrentUser,
    course_id: uuid.UUID | None = None,
    exam_id: uuid.UUID | None = None,
    status: JobStatus | None = None,
    limit: int = Query(100, ge=1, le=300),
):
    q = select(ProcessingJob)
    if exam_id:
        access.get_exam(db, user, exam_id)
        q = q.where(ProcessingJob.exam_id == exam_id)
    elif course_id:
        access.get_course(db, user, course_id)
        q = q.where(ProcessingJob.course_id == course_id)
    else:
        q = q.where(ProcessingJob.course_id.in_(access.course_ids_for(db, user)))
    if status:
        q = q.where(ProcessingJob.status == status)
    rows = db.scalars(q.order_by(ProcessingJob.queued_at.desc()).limit(limit)).all()
    return _out(db, list(rows))


def _get_job(db, user, job_id: uuid.UUID, min_role=CourseRole.VIEWER) -> ProcessingJob:
    job = db.get(ProcessingJob, job_id)
    if job is None or job.course_id is None:
        raise NotFound("Job not found.")
    access.get_course(db, user, job.course_id, min_role)
    return job


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: uuid.UUID, db: DB, user: CurrentUser):
    job = _get_job(db, user, job_id)
    return _out(db, [job])[0]


@router.post("/{job_id}/retry", response_model=JobOut, status_code=202)
def retry(job_id: uuid.UUID, db: DB, user: CurrentUser):
    job = _get_job(db, user, job_id, CourseRole.INSTRUCTOR)
    new = jobs.retry_job(db, job, user.id)
    return _out(db, [new])[0]
