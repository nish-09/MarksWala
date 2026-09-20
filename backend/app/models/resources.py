"""Course resources, their stored files and the text chunks derived from them."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text as sql_text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, SoftDeleteMixin, TimestampMixin, enum_col, uuid_pk
from app.models.enums import ResourceStatus


class Resource(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "resources"

    id: Mapped[uuid.UUID] = uuid_pk()
    course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True)
    uploaded_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)  # pdf | pptx | docx | text | image
    status: Mapped[ResourceStatus] = enum_col(ResourceStatus, nullable=False, default=ResourceStatus.UPLOADED)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)

    files: Mapped[list["ResourceFile"]] = relationship(back_populates="resource", cascade="all, delete-orphan")

    __table_args__ = (
        Index("uq_resources_course_sha", "course_id", "content_sha256", unique=True, postgresql_where=sql_text("deleted_at IS NULL")),
    )


class ResourceFile(Base):
    __tablename__ = "resource_files"

    id: Mapped[uuid.UUID] = uuid_pk()
    resource_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resources.id", ondelete="CASCADE"), nullable=False, index=True)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    original_filename: Mapped[str] = mapped_column(String(300), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=sql_text("now()"), nullable=False)

    resource: Mapped[Resource] = relationship(back_populates="files")


class ResourceChunk(Base):
    """One retrievable passage. `id` doubles as the Qdrant point id."""

    __tablename__ = "resource_chunks"

    id: Mapped[uuid.UUID] = uuid_pk()
    resource_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resources.id", ondelete="CASCADE"), nullable=False)
    course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True)
    resource_file_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("resource_files.id", ondelete="SET NULL"))
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer)
    slide_number: Mapped[int | None] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(String(300))
    text: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    embedded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("resource_id", "chunk_index", name="uq_resource_chunks_resource_idx"),)
