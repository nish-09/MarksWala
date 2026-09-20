from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from app.api.deps import DB, CurrentUser, client_ip
from app.core.errors import Conflict, NotFound, Unprocessable
from app.models.enums import CourseRole, JobKind, RubricVersionStatus
from app.schemas.common import Message
from app.schemas.jobs import JobOut
from app.schemas.rubrics import RubricOut, RubricPut
from app.services import access, audit, jobs, rubric_service

router = APIRouter(tags=["rubrics"])


class GenerateIn(BaseModel):
    replace_draft: bool = False


@router.get("/exams/{exam_id}/rubric", response_model=RubricOut)
def get_rubric(exam_id: uuid.UUID, db: DB, user: CurrentUser, version_id: uuid.UUID | None = Query(default=None)):
    exam = access.get_exam(db, user, exam_id)
    return rubric_service.build_rubric_out(db, exam, version_id)


@router.post("/exams/{exam_id}/rubric/generate", response_model=JobOut, status_code=202)
def generate(exam_id: uuid.UUID, body: GenerateIn, request: Request, db: DB, user: CurrentUser):
    exam = access.get_exam(db, user, exam_id, CourseRole.INSTRUCTOR)
    if exam.questions_confirmed_at is None:
        raise Unprocessable("Confirm the parsed questions before generating a rubric.", code="questions_not_confirmed")
    draft = rubric_service.draft_version(db, exam.id)
    if draft is not None:
        if not body.replace_draft:
            raise Conflict("A draft rubric already exists. Edit it, or generate again with replace_draft to discard it.", code="draft_exists")
        db.delete(draft)  # a DRAFT version (and its criteria) may be discarded; approved versions never can
        db.flush()
    job = jobs.create_job(db, kind=JobKind.GENERATE_RUBRIC, entity_type="exam", entity_id=exam.id, course_id=exam.course_id,
                          exam_id=exam.id, user_id=user.id)
    audit.record(db, actor_id=user.id, action="rubric.generate", entity_type="exam", entity_id=exam.id, course_id=exam.course_id, exam_id=exam.id,
                 after={"replace_draft": body.replace_draft}, ip=client_ip(request))
    db.commit()
    jobs.dispatch(job.id)
    return JobOut.model_validate(job)


@router.put("/exams/{exam_id}/rubric/draft", response_model=RubricOut)
def edit_draft(exam_id: uuid.UUID, body: RubricPut, request: Request, db: DB, user: CurrentUser):
    exam = access.get_exam(db, user, exam_id, CourseRole.INSTRUCTOR)
    draft = rubric_service.replace_draft_criteria(db, exam, body, user.id)
    audit.record(db, actor_id=user.id, action="rubric.edit", entity_type="rubric_version", entity_id=draft.id, course_id=exam.course_id, exam_id=exam.id,
                 after={"units": len(body.units), "criteria": sum(len(u.criteria) for u in body.units)}, ip=client_ip(request))
    db.commit()
    return rubric_service.build_rubric_out(db, exam, draft.id)


@router.post("/exams/{exam_id}/rubric/draft/approve", response_model=RubricOut)
def approve(exam_id: uuid.UUID, request: Request, db: DB, user: CurrentUser):
    exam = access.get_exam(db, user, exam_id, CourseRole.INSTRUCTOR)
    if exam.questions_confirmed_at is None:
        raise Unprocessable("Confirm the parsed questions before approving a rubric.", code="questions_not_confirmed")
    v = rubric_service.approve_draft(db, exam, user.id)
    audit.record(db, actor_id=user.id, action="rubric.approve", entity_type="rubric_version", entity_id=v.id, course_id=exam.course_id, exam_id=exam.id,
                 after={"version_number": v.version_number}, ip=client_ip(request))
    db.commit()
    return rubric_service.build_rubric_out(db, exam, v.id)


@router.post("/exams/{exam_id}/rubric/versions", response_model=RubricOut, status_code=201)
def new_version(exam_id: uuid.UUID, request: Request, db: DB, user: CurrentUser):
    """Start an editable draft copied from the latest approved version (approved versions are never modified)."""
    exam = access.get_exam(db, user, exam_id, CourseRole.INSTRUCTOR)
    v = rubric_service.new_draft_from_latest(db, exam, user.id)
    audit.record(db, actor_id=user.id, action="rubric.new_version", entity_type="rubric_version", entity_id=v.id, course_id=exam.course_id, exam_id=exam.id,
                 after={"version_number": v.version_number}, ip=client_ip(request))
    db.commit()
    return rubric_service.build_rubric_out(db, exam, v.id)


@router.delete("/exams/{exam_id}/rubric/draft", response_model=Message)
def discard_draft(exam_id: uuid.UUID, request: Request, db: DB, user: CurrentUser):
    exam = access.get_exam(db, user, exam_id, CourseRole.INSTRUCTOR)
    draft = rubric_service.draft_version(db, exam.id)
    if draft is None or draft.status != RubricVersionStatus.DRAFT:
        raise NotFound("There is no draft rubric.")
    db.delete(draft)
    audit.record(db, actor_id=user.id, action="rubric.discard_draft", entity_type="rubric_version", entity_id=draft.id, course_id=exam.course_id, exam_id=exam.id, ip=client_ip(request))
    db.commit()
    return Message(message="Draft discarded.")
