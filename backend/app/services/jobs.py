"""Background job lifecycle.

`processing_jobs` in PostgreSQL is the source of truth (status, progress, errors). Redis/Dramatiq only
carries a message with the job id. A worker atomically *claims* a QUEUED job, so duplicate deliveries,
redeliveries after a crash and manual retries can never run the same job twice concurrently.
"""
from __future__ import annotations

import logging
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import Conflict, ServiceUnavailable
from app.db.session import SessionLocal, session_scope
from app.models import ProcessingJob
from app.models.enums import JobKind, JobStatus
from app.providers.base import ProviderError

log = logging.getLogger("MarksWala.jobs")

FRIENDLY = {
    "ai_rate_limited": "The AI service is busy or over its quota. This will be retried automatically; if it keeps failing, retry later.",
    "ai_quota_exhausted": "The AI provider's usage quota is used up (it usually resets daily). Retry later, or an administrator can switch the model or API key.",
    "ai_timeout": "The AI service took too long to respond.",
    "ai_unavailable": "The AI service is temporarily unavailable.",
    "ai_auth_error": "The AI service rejected the server's credentials. An administrator must check the API key.",
    "ai_malformed_output": "The AI returned an unusable response several times. Try again, or review the input manually.",
    "vector_store_unavailable": "The vector database is unavailable.",
}


def describe_error(e: BaseException) -> str:
    """The user-safe message for any exception raised inside a handler (never leaks internals)."""
    if isinstance(e, JobError):
        return e.message
    if isinstance(e, ProviderError):
        return FRIENDLY.get(e.code, "An AI service error occurred.")
    return "An unexpected error occurred while processing. It has been logged."


class JobError(Exception):
    """A failure with a user-safe message. `retryable` failures are retried with backoff."""

    def __init__(self, message: str, *, code: str = "job_failed", retryable: bool = False):
        super().__init__(message)
        self.message = message
        self.code = code
        self.retryable = retryable


@dataclass
class JobResult:
    status: JobStatus = JobStatus.COMPLETED
    message: str | None = None


class JobContext:
    def __init__(self, job: ProcessingJob):
        self.job_id = job.id
        self.kind = job.kind
        self.entity_id = job.entity_id
        self.course_id = job.course_id
        self.exam_id = job.exam_id
        self.payload = job.payload or {}
        self.created_by = job.created_by
        self.attempt = job.attempts

    def progress(self, fraction: float | None, message: str | None = None) -> None:
        """Persist honest progress immediately (own short transaction) and refresh the heartbeat."""
        with session_scope() as db:
            db.execute(
                update(ProcessingJob)
                .where(ProcessingJob.id == self.job_id)
                .values(
                    progress=None if fraction is None else max(0.0, min(1.0, float(fraction))),
                    progress_message=(message or None) and message[:300],
                    heartbeat_at=datetime.now(timezone.utc),
                )
            )


Handler = Callable[[JobContext], "JobResult | None"]
_HANDLERS: dict[JobKind, Handler] = {}


def handler(kind: JobKind):
    def deco(fn: Handler) -> Handler:
        _HANDLERS[kind] = fn
        return fn

    return deco


_HANDLER_MODULES = {
    JobKind.PROCESS_RESOURCE: "app.services.resource_service",
    JobKind.PARSE_QUESTION_PAPER: "app.services.paper_service",
    JobKind.GENERATE_RUBRIC: "app.services.rubric_service",
    JobKind.PROCESS_ANSWER_SHEET: "app.services.sheet_service",
    JobKind.EVALUATE_ANSWER_SHEET: "app.services.evaluation_service",
    JobKind.GENERATE_FEEDBACK: "app.services.feedback_service",
}


def _load_handler(kind: JobKind) -> Handler | None:
    """Importing a service module registers its @handler; only the needed module is imported."""
    import importlib

    if kind not in _HANDLERS and kind in _HANDLER_MODULES:
        importlib.import_module(_HANDLER_MODULES[kind])
    return _HANDLERS.get(kind)


# ---------------------------------------------------------------------------------------------------
# Creating + dispatching (API side)
# ---------------------------------------------------------------------------------------------------
def create_job(
    db: Session,
    *,
    kind: JobKind,
    entity_type: str,
    entity_id: uuid.UUID,
    course_id: uuid.UUID | None,
    exam_id: uuid.UUID | None,
    user_id: uuid.UUID | None,
    payload: dict | None = None,
) -> ProcessingJob:
    """Insert a QUEUED job. The partial unique index rejects a second active job for the same entity+kind."""
    job = ProcessingJob(
        kind=kind,
        entity_type=entity_type,
        entity_id=entity_id,
        course_id=course_id,
        exam_id=exam_id,
        created_by=user_id,
        payload=payload,
        status=JobStatus.QUEUED,
        max_attempts=settings.job_max_attempts,
    )
    try:
        with db.begin_nested():
            db.add(job)
            db.flush()
    except IntegrityError as e:
        existing = db.scalar(
            select(ProcessingJob).where(
                ProcessingJob.kind == kind,
                ProcessingJob.entity_id == entity_id,
                ProcessingJob.status.in_([JobStatus.QUEUED, JobStatus.PROCESSING]),
            )
        )
        raise Conflict(
            "This is already being processed. Wait for it to finish or check the Processing page.",
            code="job_already_active",
            details={"job_id": str(existing.id)} if existing else None,
        ) from e
    return job


def dispatch(job_id: uuid.UUID, *, delay_ms: int = 0) -> None:
    """Send the queue message. Call only AFTER the job row is committed."""
    from app.tasks.actors import run_job_actor

    try:
        if delay_ms:
            run_job_actor.send_with_options(args=(str(job_id),), delay=delay_ms)
        else:
            run_job_actor.send(str(job_id))
    except Exception as e:
        log.error("could not enqueue job %s: %s", job_id, e)
        with session_scope() as db:
            db.execute(
                update(ProcessingJob)
                .where(ProcessingJob.id == job_id, ProcessingJob.status == JobStatus.QUEUED)
                .values(
                    status=JobStatus.FAILED,
                    error_code="queue_unavailable",
                    error_message="The background queue (Redis) is unavailable, so the job was not started. Please retry.",
                    completed_at=datetime.now(timezone.utc),
                )
            )
        raise ServiceUnavailable(
            "The background queue is unavailable, so processing could not start. Please try again in a moment.",
            code="queue_unavailable",
        ) from e


def enqueue(db: Session, **kw) -> ProcessingJob:
    """create_job + commit + dispatch."""
    job = create_job(db, **kw)
    db.commit()
    dispatch(job.id)
    return job


def retry_job(db: Session, job: ProcessingJob, user_id: uuid.UUID) -> ProcessingJob:
    """Manually re-run a FAILED job as a fresh job for the same entity (handlers are idempotent)."""
    if job.status != JobStatus.FAILED:
        raise Conflict("Only failed jobs can be retried.", code="job_not_failed")
    return enqueue(
        db,
        kind=job.kind,
        entity_type=job.entity_type,
        entity_id=job.entity_id,
        course_id=job.course_id,
        exam_id=job.exam_id,
        user_id=user_id,
        payload=job.payload,
    )


# ---------------------------------------------------------------------------------------------------
# Running (worker side)
# ---------------------------------------------------------------------------------------------------
def _claim(job_id: uuid.UUID) -> ProcessingJob | None:
    with session_scope() as db:
        row = db.execute(
            text(
                """UPDATE processing_jobs
                   SET status='PROCESSING', started_at=COALESCE(started_at, now()), heartbeat_at=now(),
                       attempts=attempts+1, progress=NULL, progress_message=NULL, error_code=NULL, error_message=NULL
                   WHERE id=:id AND status='QUEUED' RETURNING id"""
            ),
            {"id": job_id},
        ).first()
        return db.get(ProcessingJob, row[0]) if row else None


def run_job(job_id: uuid.UUID) -> None:
    job = _claim(job_id)
    if job is None:
        log.info("job %s not claimable (already running/finished); ignoring duplicate delivery", job_id)
        return
    ctx = JobContext(job)
    fn = _load_handler(job.kind)
    if fn is None:
        _finish(job_id, JobStatus.FAILED, error_code="no_handler", error_message=f"No handler registered for {job.kind.value}.")
        return
    log.info("job %s %s started (attempt %d)", job_id, job.kind.value, job.attempts)
    try:
        result = fn(ctx) or JobResult()
        _finish(job_id, result.status, progress=1.0, message=result.message)
        log.info("job %s %s -> %s", job_id, job.kind.value, result.status.value)
    except JobError as e:
        _fail_or_retry(job, e.code, e.message, e.retryable)
    except ProviderError as e:  # includes AIAuthError (not retryable)
        _fail_or_retry(job, e.code, describe_error(e), e.retryable)
    except Exception:  # never leak internals; log the full trace server-side
        log.error("job %s crashed:\n%s", job_id, traceback.format_exc())
        _fail_or_retry(job, "internal_error", "An unexpected error occurred while processing. It has been logged.", False)


def _finish(job_id, status: JobStatus, *, progress=None, message=None, error_code=None, error_message=None) -> None:
    with session_scope() as db:
        db.execute(
            update(ProcessingJob)
            .where(ProcessingJob.id == job_id)
            .values(
                status=status,
                progress=progress,
                progress_message=(message or None) and message[:300],
                error_code=error_code,
                error_message=error_message,
                completed_at=datetime.now(timezone.utc),
            )
        )


def _fail_or_retry(job: ProcessingJob, code: str, message: str, retryable: bool) -> None:
    if retryable and job.attempts < job.max_attempts:
        delay = min(15 * (2 ** (job.attempts - 1)), 240) * 1000
        with session_scope() as db:
            db.execute(
                update(ProcessingJob)
                .where(ProcessingJob.id == job.id)
                .values(
                    status=JobStatus.QUEUED,
                    error_code=code,
                    error_message=f"{message} (retrying, attempt {job.attempts}/{job.max_attempts})",
                    queued_at=datetime.now(timezone.utc),
                )
            )
        log.warning("job %s failed (%s); retrying in %.0fs", job.id, code, delay / 1000)
        try:
            dispatch(job.id, delay_ms=delay)
        except ServiceUnavailable:
            pass  # dispatch already marked the job FAILED
    else:
        _finish(job.id, JobStatus.FAILED, error_code=code, error_message=message)
        log.error("job %s FAILED (%s): %s", job.id, code, message)


def reap_stale_jobs() -> int:
    """Recover from worker crashes: PROCESSING jobs with no heartbeat, and QUEUED jobs whose message was lost."""
    now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(minutes=settings.stale_job_minutes)
    lost_cutoff = now - timedelta(minutes=5)
    n = 0
    with session_scope() as db:
        crashed = db.scalars(
            select(ProcessingJob).where(
                ProcessingJob.status == JobStatus.PROCESSING,
                text("COALESCE(heartbeat_at, started_at) < :c").bindparams(c=stale_cutoff),
            )
        ).all()
        lost = db.scalars(
            select(ProcessingJob).where(ProcessingJob.status == JobStatus.QUEUED, ProcessingJob.queued_at < lost_cutoff)
        ).all()
        redispatch: list[uuid.UUID] = []
        for j in crashed:
            n += 1
            if j.attempts < j.max_attempts:
                j.status = JobStatus.QUEUED
                j.queued_at = now
                j.error_message = "Recovered after the worker stopped responding; retrying."
                redispatch.append(j.id)
            else:
                j.status = JobStatus.FAILED
                j.error_code = "worker_crash"
                j.error_message = "The worker stopped responding repeatedly while processing this job."
                j.completed_at = now
        for j in lost:
            n += 1
            j.queued_at = now
            redispatch.append(j.id)
    for jid in redispatch:
        try:
            dispatch(jid)
        except ServiceUnavailable:
            pass
    if n:
        log.warning("reaper recovered %d job(s)", n)
    return n


def sync_get(job_id: uuid.UUID) -> ProcessingJob | None:
    with SessionLocal() as db:
        return db.get(ProcessingJob, job_id)
