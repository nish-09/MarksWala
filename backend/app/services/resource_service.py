"""Course resource ingestion: upload -> store -> extract -> chunk -> embed -> Qdrant -> verify -> COMPLETED.

A resource is only COMPLETED after its vectors are confirmed present in Qdrant.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import UploadFile
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import Conflict
from app.db.session import session_scope
from app.models import Course, Resource, ResourceChunk, ResourceFile, User
from app.models.enums import JobKind, ResourceStatus
from app.providers import registry
from app.providers.base import VectorPoint
from app.providers.storage import get_storage
from app.services import audit, documents
from app.services.files import store_upload
from app.services.jobs import JobContext, JobError, JobResult, describe_error, handler

log = logging.getLogger("MarksWala.resources")

RESOURCE_KINDS = {"pdf", "pptx", "docx", "text", "png", "jpeg"}


def create_from_upload(db: Session, user: User, course: Course, upload: UploadFile, title: str | None, ip: str | None) -> Resource:
    storage = get_storage()
    stored = store_upload(
        upload,
        storage,
        prefix=f"resources/{course.id}",
        allowed_kinds=RESOURCE_KINDS,
        max_bytes=settings.max_resource_upload_mb * 1024 * 1024,
    )
    display_title = (title or "").strip() or stored.original_filename.rsplit(".", 1)[0]
    res = Resource(
        course_id=course.id,
        uploaded_by=user.id,
        title=display_title[:300],
        kind="image" if stored.kind in {"png", "jpeg"} else stored.kind,
        status=ResourceStatus.UPLOADED,
        content_sha256=stored.sha256,
    )
    db.add(res)
    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        storage.delete(stored.storage_key)
        raise Conflict("This exact file has already been uploaded to this course.", code="duplicate_resource") from e
    db.add(
        ResourceFile(
            resource_id=res.id,
            storage_key=stored.storage_key,
            original_filename=stored.original_filename,
            mime_type=stored.mime_type,
            size_bytes=stored.size_bytes,
            sha256=stored.sha256,
            page_count=stored.page_count,
        )
    )
    audit.record(db, actor_id=user.id, action="resource.upload", entity_type="resource", entity_id=res.id, course_id=course.id,
                 after={"title": res.title, "kind": res.kind, "size_bytes": stored.size_bytes}, ip=ip)
    return res


def remove_resource(db: Session, resource: Resource) -> None:
    """Soft-delete, remove vectors and the stored file. Vector cleanup failure must not silently leave orphans."""
    registry.get_vector_store().delete_resource(resource.id)
    for f in resource.files:
        get_storage().delete(f.storage_key)
    db.execute(delete(ResourceChunk).where(ResourceChunk.resource_id == resource.id))
    resource.deleted_at = datetime.now(timezone.utc)


def _set_status(resource_id: uuid.UUID, status: ResourceStatus, error: str | None = None, **extra) -> None:
    with session_scope() as db:
        db.execute(update(Resource).where(Resource.id == resource_id).values(status=status, error_message=error, **extra))


@handler(JobKind.PROCESS_RESOURCE)
def process_resource(ctx: JobContext) -> JobResult:
    rid = ctx.entity_id
    storage = get_storage()
    embedder = registry.get_embedder()
    store = registry.get_vector_store()

    # 1. load + reset any earlier attempt (idempotent)
    with session_scope() as db:
        res = db.get(Resource, rid)
        if res is None or res.deleted_at is not None:
            raise JobError("The resource no longer exists.", code="resource_missing")
        f = db.scalar(select(ResourceFile).where(ResourceFile.resource_id == rid))
        if f is None:
            raise JobError("The resource has no stored file.", code="file_missing")
        course_id, title, kind, key, mime = res.course_id, res.title, res.kind, f.storage_key, f.mime_type
        res.status = ResourceStatus.PROCESSING
        res.error_message = None
        db.execute(delete(ResourceChunk).where(ResourceChunk.resource_id == rid))
        file_id = f.id

    try:
        store.delete_resource(rid)
        ctx.progress(None, "Reading document")
        data = storage.read_bytes(key)
        try:
            if kind == "pdf":
                units, ocr_pages = documents.extract_pdf(
                    data,
                    registry.get_ocr(),
                    on_page=lambda i, n: ctx.progress(0.05 + 0.15 * i / n, f"Extracting page {i}/{n}"),
                )
            elif kind == "pptx":
                units = documents.extract_pptx(data)
            elif kind == "docx":
                units = documents.extract_docx(data)
            elif kind == "text":
                units = documents.extract_text(data)
            else:
                units = documents.extract_image(data, registry.get_ocr(), mime)
        except documents.ExtractionError as e:
            raise JobError(str(e), code="extraction_failed") from e
        except JobError:
            raise
        except Exception as e:
            if type(e).__module__.startswith(("fitz", "pymupdf", "pptx", "docx", "zipfile")) or isinstance(e, (ValueError, KeyError)):
                raise JobError(f"The document could not be read ({type(e).__name__}). It may be corrupted.", code="unreadable_document") from e
            raise

        chunks = documents.chunk_units(units)
        if not chunks:
            raise JobError(
                "No readable text was found in this file. If it is a scan, make sure it is legible and try again.",
                code="no_text",
            )

        # 2. persist chunks (embedded_at is set only after Qdrant confirms the vectors)
        chunk_ids = [uuid.uuid4() for _ in chunks]
        with session_scope() as db:
            for i, (cid, ch) in enumerate(zip(chunk_ids, chunks)):
                db.add(
                    ResourceChunk(
                        id=cid, resource_id=rid, course_id=course_id, resource_file_id=file_id, chunk_index=i,
                        page_number=ch.page, slide_number=ch.slide, section=(ch.section or None) and ch.section[:300],
                        text=ch.text, token_count=ch.token_count,
                    )
                )

        # 3. embed + upsert, reporting real progress
        store.ensure_collection(embedder.dimensions)
        batch = 32
        for start in range(0, len(chunks), batch):
            part = chunks[start : start + batch]
            vecs = embedder.embed_documents(
                [f"{title}" + (f" — {c.section}" if c.section else "") + f"\n{c.text}" for c in part]
            )
            store.upsert(
                [
                    VectorPoint(
                        id=chunk_ids[start + j],
                        vector=vecs[j],
                        payload={
                            "course_id": str(course_id),
                            "resource_id": str(rid),
                            "chunk_id": str(chunk_ids[start + j]),
                            "page_number": part[j].page,
                            "slide_number": part[j].slide,
                            "section": part[j].section,
                        },
                    )
                    for j in range(len(part))
                ]
            )
            ctx.progress(0.2 + 0.75 * min(start + batch, len(chunks)) / len(chunks), f"Embedded {min(start + batch, len(chunks))}/{len(chunks)} passages")

        # 4. verify before claiming success
        stored = store.count_resource(rid)
        if stored != len(chunks):
            raise JobError(
                f"Vector storage verification failed: expected {len(chunks)} vectors but found {stored}.",
                code="vector_verification_failed",
                retryable=True,
            )
        with session_scope() as db:
            db.execute(update(ResourceChunk).where(ResourceChunk.resource_id == rid).values(embedded_at=datetime.now(timezone.utc)))
            db.execute(
                update(Resource)
                .where(Resource.id == rid)
                .values(status=ResourceStatus.COMPLETED, chunk_count=len(chunks), processed_at=datetime.now(timezone.utc), error_message=None)
            )
        return JobResult(message=f"{len(chunks)} passages indexed")
    except BaseException as e:
        _set_status(rid, ResourceStatus.FAILED, describe_error(e))
        raise
