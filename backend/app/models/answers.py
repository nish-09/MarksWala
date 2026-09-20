"""Answer sheets, pages, OCR output, extracted answers and the page→question mapping."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, SoftDeleteMixin, TimestampMixin, enum_col, uuid_pk
from app.models.enums import BatchStatus, MappingMethod, SheetStatus


class AnswerSheetBatch(Base, TimestampMixin):
    __tablename__ = "answer_sheet_batches"

    id: Mapped[uuid.UUID] = uuid_pk()
    exam_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("exams.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    status: Mapped[BatchStatus] = enum_col(BatchStatus, nullable=False, default=BatchStatus.OPEN)
    label: Mapped[str | None] = mapped_column(String(200))


class AnswerSheet(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "answer_sheets"

    id: Mapped[uuid.UUID] = uuid_pk()
    exam_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("exams.id", ondelete="CASCADE"), nullable=False, index=True)
    batch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("answer_sheet_batches.id", ondelete="SET NULL"))
    student_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("students.id", ondelete="SET NULL"), index=True)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    original_filename: Mapped[str] = mapped_column(String(300), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[SheetStatus] = enum_col(SheetStatus, nullable=False, default=SheetStatus.UPLOADED)
    detected_name: Mapped[str | None] = mapped_column(String(200))
    detected_roll_number: Mapped[str | None] = mapped_column(String(64))
    identity_confidence: Mapped[float | None] = mapped_column(Float)
    error_message: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    pages: Mapped[list["AnswerPage"]] = relationship(
        back_populates="sheet", cascade="all, delete-orphan", order_by="AnswerPage.page_number"
    )

    __table_args__ = (
        # the same PDF cannot be uploaded twice for one exam
        Index("uq_answer_sheets_exam_sha", "exam_id", "sha256", unique=True, postgresql_where=sql_text("deleted_at IS NULL")),
        # a student has at most one live answer sheet per exam -> answers can never bleed between students
        Index(
            "uq_answer_sheets_exam_student",
            "exam_id",
            "student_id",
            unique=True,
            postgresql_where=sql_text("deleted_at IS NULL AND student_id IS NOT NULL"),
        ),
    )


class AnswerPage(Base):
    __tablename__ = "answer_pages"

    id: Mapped[uuid.UUID] = uuid_pk()
    answer_sheet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("answer_sheets.id", ondelete="CASCADE"), nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)  # 1-based
    image_key: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=sql_text("now()"), nullable=False)

    sheet: Mapped[AnswerSheet] = relationship(back_populates="pages")
    ocr_results: Mapped[list["OcrResult"]] = relationship(back_populates="page", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("answer_sheet_id", "page_number", name="uq_answer_pages_sheet_page"),)


class OcrResult(Base):
    """Raw OCR for one page. `text` is immutable (enforced by a DB trigger) — corrections live on `answers`."""

    __tablename__ = "ocr_results"

    id: Mapped[uuid.UUID] = uuid_pk()
    answer_page_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("answer_pages.id", ondelete="CASCADE"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    has_diagram: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    unreadable_spans: Mapped[list | None] = mapped_column(JSONB)
    # structured segmentation produced from this OCR: [{label, text, confidence, continuation}, ...]
    segments: Mapped[list | None] = mapped_column(JSONB)
    header: Mapped[dict | None] = mapped_column(JSONB)  # student name / roll number found on the page
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=sql_text("now()"), nullable=False)

    page: Mapped[AnswerPage] = relationship(back_populates="ocr_results")

    __table_args__ = (
        UniqueConstraint("answer_page_id", "version", name="uq_ocr_results_page_version"),
        Index("uq_ocr_results_current", "answer_page_id", unique=True, postgresql_where=sql_text("is_current")),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
    )


class Answer(Base, TimestampMixin):
    """The student's answer to one gradable unit (a question, or one of its sub-parts)."""

    __tablename__ = "answers"

    id: Mapped[uuid.UUID] = uuid_pk()
    answer_sheet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("answer_sheets.id", ondelete="CASCADE"), nullable=False, index=True)
    question_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("questions.id"), nullable=False, index=True)
    subquestion_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("subquestions.id"))
    original_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    corrected_text: Mapped[str | None] = mapped_column(Text)
    corrected_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    corrected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_missing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    has_diagram: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ocr_confidence: Mapped[float | None] = mapped_column(Float)
    mapping_confidence: Mapped[float | None] = mapped_column(Float)

    mappings: Mapped[list["AnswerMapping"]] = relationship(back_populates="answer")

    __table_args__ = (
        Index(
            "uq_answers_unit_question",
            "answer_sheet_id",
            "question_id",
            unique=True,
            postgresql_where=sql_text("subquestion_id IS NULL"),
        ),
        Index(
            "uq_answers_unit_sub",
            "answer_sheet_id",
            "question_id",
            "subquestion_id",
            unique=True,
            postgresql_where=sql_text("subquestion_id IS NOT NULL"),
        ),
    )

    @property
    def effective_text(self) -> str:
        return self.corrected_text if self.corrected_text is not None else self.original_text


class AnswerMapping(Base):
    """Links a segment of a page's OCR text to the answer it belongs to (many pages -> one answer)."""

    __tablename__ = "answer_mappings"

    id: Mapped[uuid.UUID] = uuid_pk()
    answer_sheet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("answer_sheets.id", ondelete="CASCADE"), nullable=False, index=True)
    # NULL = an unassigned segment (no confident question match); a teacher can assign it later
    answer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("answers.id", ondelete="SET NULL"), index=True)
    answer_page_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("answer_pages.id", ondelete="CASCADE"), nullable=False, index=True)
    ocr_result_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ocr_results.id", ondelete="CASCADE"), nullable=False)
    segment_order: Mapped[int] = mapped_column(Integer, nullable=False)  # global order across the sheet
    detected_label: Mapped[str | None] = mapped_column(String(50))
    text: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    method: Mapped[MappingMethod] = enum_col(MappingMethod, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=sql_text("now()"), nullable=False)

    answer: Mapped[Answer | None] = relationship(back_populates="mappings")

    __table_args__ = (CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),)


__all__ = [
    "AnswerSheetBatch",
    "AnswerSheet",
    "AnswerPage",
    "OcrResult",
    "Answer",
    "AnswerMapping",
]
