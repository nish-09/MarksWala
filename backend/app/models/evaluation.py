"""AI evaluations, criterion scores, confidence flags, teacher review/overrides and results."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
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

from app.db.base import Base, TimestampMixin, enum_col, marks_col, uuid_pk
from app.models.enums import (
    EvaluationStatus,
    FlagKind,
    FlagSeverity,
    ResultStatus,
    ReviewDecision,
    ReviewStatus,
    TextSource,
)


class Evaluation(Base):
    """One AI evaluation of one answer against one rubric version. Never mutated after creation
    (except `is_current` when a newer evaluation supersedes it) — teacher changes go to TeacherOverride."""

    __tablename__ = "evaluations"

    id: Mapped[uuid.UUID] = uuid_pk()
    answer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("answers.id", ondelete="CASCADE"), nullable=False, index=True)
    rubric_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rubric_versions.id"), nullable=False, index=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[EvaluationStatus] = enum_col(EvaluationStatus, nullable=False)
    text_source: Mapped[TextSource] = enum_col(TextSource, nullable=False)
    answer_text_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(40), nullable=False)
    ai_total: Mapped[Decimal] = marks_col(nullable=False)  # computed in the backend from criterion scores
    max_total: Mapped[Decimal] = marks_col(nullable=False)
    overall_feedback: Mapped[str | None] = mapped_column(Text)

    ocr_confidence: Mapped[float | None] = mapped_column(Float)
    mapping_confidence: Mapped[float | None] = mapped_column(Float)
    retrieval_confidence: Mapped[float | None] = mapped_column(Float)
    rubric_confidence: Mapped[float | None] = mapped_column(Float)
    evaluation_confidence: Mapped[float | None] = mapped_column(Float)
    overall_confidence: Mapped[float | None] = mapped_column(Float)

    requires_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    raw_output: Mapped[dict | None] = mapped_column(JSONB)  # the validated structured AI response
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=sql_text("now()"), nullable=False)

    criteria: Mapped[list["EvaluationCriterion"]] = relationship(
        back_populates="evaluation", cascade="all, delete-orphan", order_by="EvaluationCriterion.position"
    )
    sources: Mapped[list["EvaluationSource"]] = relationship(
        back_populates="evaluation", cascade="all, delete-orphan", order_by="EvaluationSource.rank"
    )

    __table_args__ = (
        UniqueConstraint("answer_id", "attempt", name="uq_evaluations_answer_attempt"),
        Index("uq_evaluations_current", "answer_id", unique=True, postgresql_where=sql_text("is_current")),
        CheckConstraint("ai_total >= 0 AND ai_total <= max_total", name="total_in_range"),
    )


class EvaluationCriterion(Base):
    __tablename__ = "evaluation_criteria"

    id: Mapped[uuid.UUID] = uuid_pk()
    evaluation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evaluations.id", ondelete="CASCADE"), nullable=False, index=True)
    rubric_criterion_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rubric_criteria.id"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[Decimal] = marks_col(nullable=False)
    max_score: Mapped[Decimal] = marks_col(nullable=False)
    satisfied: Mapped[bool] = mapped_column(Boolean, nullable=False)
    partial: Mapped[bool] = mapped_column(Boolean, nullable=False)
    evidence: Mapped[str | None] = mapped_column(Text)
    missing_points: Mapped[str | None] = mapped_column(Text)
    feedback: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)

    evaluation: Mapped[Evaluation] = relationship(back_populates="criteria")

    __table_args__ = (
        UniqueConstraint("evaluation_id", "rubric_criterion_id", name="uq_evaluation_criteria_eval_criterion"),
        CheckConstraint("score >= 0 AND score <= max_score", name="score_in_range"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
    )


class EvaluationSource(Base):
    """Retrieval provenance: which course chunks the evaluator was shown."""

    __tablename__ = "evaluation_sources"

    id: Mapped[uuid.UUID] = uuid_pk()
    evaluation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evaluations.id", ondelete="CASCADE"), nullable=False, index=True)
    chunk_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("resource_chunks.id", ondelete="SET NULL"))
    resource_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("resources.id", ondelete="SET NULL"))
    resource_title: Mapped[str] = mapped_column(String(300), nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer)
    slide_number: Mapped[int | None] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(String(300))
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    text_snapshot: Mapped[str] = mapped_column(Text, nullable=False)

    evaluation: Mapped[Evaluation] = relationship(back_populates="sources")


class ConfidenceFlag(Base):
    __tablename__ = "confidence_flags"

    id: Mapped[uuid.UUID] = uuid_pk()
    answer_sheet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("answer_sheets.id", ondelete="CASCADE"), nullable=False, index=True)
    answer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("answers.id", ondelete="CASCADE"), index=True)
    evaluation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("evaluations.id", ondelete="CASCADE"), index=True)
    kind: Mapped[FlagKind] = enum_col(FlagKind, nullable=False)
    severity: Mapped[FlagSeverity] = enum_col(FlagSeverity, nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[float | None] = mapped_column(Float)
    threshold: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=sql_text("now()"), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TeacherReview(Base, TimestampMixin):
    """The review task for one evaluation (created when it is flagged, or lazily on first teacher action)."""

    __tablename__ = "teacher_reviews"

    id: Mapped[uuid.UUID] = uuid_pk()
    evaluation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evaluations.id", ondelete="CASCADE"), nullable=False, unique=True)
    answer_sheet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("answer_sheets.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[ReviewStatus] = enum_col(ReviewStatus, nullable=False, default=ReviewStatus.PENDING)
    mandatory: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    decision: Mapped[ReviewDecision | None] = enum_col(ReviewDecision, nullable=True)
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    notes: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TeacherOverride(Base):
    """Append-only history of teacher score changes for one criterion. The AI score is never touched."""

    __tablename__ = "teacher_overrides"

    id: Mapped[uuid.UUID] = uuid_pk()
    evaluation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evaluations.id", ondelete="CASCADE"), nullable=False, index=True)
    evaluation_criterion_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluation_criteria.id", ondelete="CASCADE"), nullable=False
    )
    review_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teacher_reviews.id", ondelete="SET NULL"))
    ai_score: Mapped[Decimal] = marks_col(nullable=False)
    teacher_score: Mapped[Decimal] = marks_col(nullable=False)
    final_score: Mapped[Decimal] = marks_col(nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    teacher_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=sql_text("now()"), nullable=False)

    __table_args__ = (
        Index("uq_teacher_overrides_active", "evaluation_criterion_id", unique=True, postgresql_where=sql_text("is_active")),
        CheckConstraint("length(btrim(reason)) > 0", name="reason_not_blank"),
        CheckConstraint("teacher_score >= 0 AND final_score >= 0", name="scores_nonneg"),
    )


class StudentResult(Base, TimestampMixin):
    __tablename__ = "student_results"

    id: Mapped[uuid.UUID] = uuid_pk()
    exam_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("exams.id", ondelete="CASCADE"), nullable=False, index=True)
    student_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("students.id", ondelete="SET NULL"))
    answer_sheet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("answer_sheets.id", ondelete="CASCADE"), nullable=False, unique=True)
    total: Mapped[Decimal] = marks_col(nullable=False)
    max_total: Mapped[Decimal] = marks_col(nullable=False)
    percentage: Mapped[Decimal] = marks_col(nullable=False)
    passed: Mapped[bool | None] = mapped_column(Boolean)
    status: Mapped[ResultStatus] = enum_col(ResultStatus, nullable=False)
    pending_reviews: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=sql_text("now()"), nullable=False)
    feedback: Mapped[dict | None] = mapped_column(JSONB)
    feedback_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (CheckConstraint("total >= 0 AND total <= max_total", name="total_in_range"),)


class TopicResult(Base):
    __tablename__ = "topic_results"

    id: Mapped[uuid.UUID] = uuid_pk()
    exam_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("exams.id", ondelete="CASCADE"), nullable=False, index=True)
    answer_sheet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("answer_sheets.id", ondelete="CASCADE"), nullable=False)
    student_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("students.id", ondelete="SET NULL"))
    topic: Mapped[str] = mapped_column(String(200), nullable=False)
    score: Mapped[Decimal] = marks_col(nullable=False)
    max_score: Mapped[Decimal] = marks_col(nullable=False)
    percentage: Mapped[Decimal] = marks_col(nullable=False)

    __table_args__ = (UniqueConstraint("answer_sheet_id", "topic", name="uq_topic_results_sheet_topic"),)


class ExamAnalytics(Base):
    __tablename__ = "exam_analytics"

    id: Mapped[uuid.UUID] = uuid_pk()
    exam_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("exams.id", ondelete="CASCADE"), nullable=False, unique=True)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=sql_text("now()"), nullable=False)
