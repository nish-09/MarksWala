from __future__ import annotations

import uuid
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.api.deps import DB, CurrentUser, client_ip
from app.core.errors import Conflict, NotFound
from app.models import ProcessingJob, Resource, ResourceChunk
from app.models.enums import CourseRole, JobKind, ResourceStatus
from app.providers import registry
from app.providers.storage import get_storage
from app.schemas.common import Message
from app.schemas.jobs import JobOut
from app.schemas.resources import ChunkOut, ResourceOut, SearchHit, SearchIn
from app.services import access, audit, jobs, resource_service, retrieval

router = APIRouter(tags=["resources"])


def latest_job(db, kind: JobKind, entity_id: uuid.UUID) -> ProcessingJob | None:
    return db.scalar(
        select(ProcessingJob).where(ProcessingJob.kind == kind, ProcessingJob.entity_id == entity_id).order_by(ProcessingJob.queued_at.desc()).limit(1)
    )


def _out(db, r: Resource) -> ResourceOut:
    out = ResourceOut.model_validate(r)
    job = latest_job(db, JobKind.PROCESS_RESOURCE, r.id)
    out.job = JobOut.model_validate(job) if job else None
    return out


@router.get("/courses/{course_id}/resources", response_model=list[ResourceOut])
def list_resources(course_id: uuid.UUID, db: DB, user: CurrentUser):
    access.get_course(db, user, course_id)
    rows = db.scalars(select(Resource).where(Resource.course_id == course_id, Resource.deleted_at.is_(None)).order_by(Resource.created_at.desc())).all()
    return [_out(db, r) for r in rows]


@router.post("/courses/{course_id}/resources", response_model=ResourceOut, status_code=201)
def upload_resource(
    course_id: uuid.UUID,
    request: Request,
    db: DB,
    user: CurrentUser,
    file: UploadFile = File(...),
    title: str | None = Form(default=None, max_length=300),
):
    course = access.get_course(db, user, course_id, CourseRole.INSTRUCTOR)
    res = resource_service.create_from_upload(db, user, course, file, title, client_ip(request))
    job = jobs.create_job(db, kind=JobKind.PROCESS_RESOURCE, entity_type="resource", entity_id=res.id,
                          course_id=course.id, exam_id=None, user_id=user.id)
    db.commit()
    jobs.dispatch(job.id)  # if the queue is down the resource stays UPLOADED and the job is marked FAILED
    db.refresh(res)
    return _out(db, res)


@router.get("/resources/{resource_id}", response_model=ResourceOut)
def get_resource(resource_id: uuid.UUID, db: DB, user: CurrentUser):
    return _out(db, access.get_resource(db, user, resource_id))


@router.get("/resources/{resource_id}/chunks", response_model=list[ChunkOut])
def list_chunks(resource_id: uuid.UUID, db: DB, user: CurrentUser, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    access.get_resource(db, user, resource_id)
    return db.scalars(
        select(ResourceChunk).where(ResourceChunk.resource_id == resource_id).order_by(ResourceChunk.chunk_index).limit(limit).offset(offset)
    ).all()


@router.post("/resources/{resource_id}/reprocess", response_model=ResourceOut, status_code=202)
def reprocess_resource(resource_id: uuid.UUID, db: DB, user: CurrentUser):
    res = access.get_resource(db, user, resource_id, CourseRole.INSTRUCTOR)
    if res.status == ResourceStatus.PROCESSING:
        raise Conflict("This resource is already being processed.", code="job_already_active")
    jobs.enqueue(db, kind=JobKind.PROCESS_RESOURCE, entity_type="resource", entity_id=res.id, course_id=res.course_id, exam_id=None, user_id=user.id)
    db.refresh(res)
    return _out(db, res)


@router.delete("/resources/{resource_id}", response_model=Message)
def delete_resource(resource_id: uuid.UUID, request: Request, db: DB, user: CurrentUser):
    res = access.get_resource(db, user, resource_id, CourseRole.INSTRUCTOR)
    active = db.scalar(select(ProcessingJob.id).where(ProcessingJob.entity_id == res.id, ProcessingJob.status.in_(["QUEUED", "PROCESSING"])))
    if active:
        raise Conflict("Wait for processing to finish before deleting this resource.", code="job_already_active")
    resource_service.remove_resource(db, res)
    audit.record(db, actor_id=user.id, action="resource.delete", entity_type="resource", entity_id=res.id, course_id=res.course_id, ip=client_ip(request))
    db.commit()
    return Message(message="Resource deleted.")


@router.get("/resources/{resource_id}/file")
def download_resource(resource_id: uuid.UUID, db: DB, user: CurrentUser):
    res = access.get_resource(db, user, resource_id)
    f = res.files[0] if res.files else None
    if f is None or not get_storage().exists(f.storage_key):
        raise NotFound("The stored file is missing.")
    return StreamingResponse(
        get_storage().open_stream(f.storage_key),
        media_type=f.mime_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(f.original_filename)}",
            "Content-Length": str(f.size_bytes),
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/courses/{course_id}/search", response_model=list[SearchHit])
def search_course(course_id: uuid.UUID, body: SearchIn, db: DB, user: CurrentUser):
    """Test retrieval against the course's indexed material (the same code path evaluation uses)."""
    access.get_course(db, user, course_id)
    hits = retrieval.retrieve(db, course_id=course_id, query=body.query, embedder=registry.get_embedder(),
                              store=registry.get_vector_store(), top_k=body.top_k, min_score=0.0)
    return [SearchHit(chunk_id=h.chunk_id, resource_id=h.resource_id, resource_title=h.resource_title, page_number=h.page_number,
                      slide_number=h.slide_number, section=h.section, score=round(h.score, 4), text=h.text) for h in hits]
