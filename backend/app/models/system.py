"""Background job tracking and the audit trail."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, text as sql_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, enum_col, uuid_pk
from app.models.enums import JobKind, JobStatus


class ProcessingJob(Base):
    """Source of truth for background work. Redis only carries the message; state lives here."""

    __tablename__ = "processing_jobs"

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[JobKind] = enum_col(JobKind, nullable=False)
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    # course/exam the job belongs to, so job listings can be authorized without joining every entity
    course_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    exam_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("exams.id", ondelete="CASCADE"), index=True)
    status: Mapped[JobStatus] = enum_col(JobStatus, nullable=False, default=JobStatus.QUEUED)
    # null = progress cannot be measured honestly -> UI shows an indeterminate state
    progress: Mapped[float | None] = mapped_column(Float)
    progress_message: Mapped[str | None] = mapped_column(String(300))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    error_code: Mapped[str | None] = mapped_column(String(60))
    error_message: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict | None] = mapped_column(JSONB)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=sql_text("now()"), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # at most one queued/processing job per (kind, entity): prevents duplicate concurrent processing
        Index(
            "uq_processing_jobs_active",
            "kind",
            "entity_id",
            unique=True,
            postgresql_where=sql_text("status IN ('QUEUED','PROCESSING')"),
        ),
        Index("ix_processing_jobs_entity", "entity_type", "entity_id"),
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = uuid_pk()
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column()
    course_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    exam_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=sql_text("now()"), nullable=False, index=True)

    __table_args__ = (Index("ix_audit_logs_entity", "entity_type", "entity_id"),)
