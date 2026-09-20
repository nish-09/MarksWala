"""Exams, the question hierarchy and versioned rubrics."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, SoftDeleteMixin, TimestampMixin, enum_col, marks_col, uuid_pk
from app.models.enums import PaperStatus, RubricSource, RubricVersionStatus


class Exam(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "exams"

    id: Mapped[uuid.UUID] = uuid_pk()
    course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    pass_percentage: Mapped[Decimal | None] = marks_col()  # null -> configured default
    confidence_high_threshold: Mapped[float | None] = mapped_column(Float)
    confidence_review_threshold: Mapped[float | None] = mapped_column(Float)

    paper_status: Mapped[PaperStatus] = enum_col(PaperStatus, nullable=False, default=PaperStatus.NONE)
    paper_storage_key: Mapped[str | None] = mapped_column(String(500))
    paper_filename: Mapped[str | None] = mapped_column(String(300))
    paper_sha256: Mapped[str | None] = mapped_column(String(64))
    paper_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    paper_error: Mapped[str | None] = mapped_column(Text)
    declared_total_marks: Mapped[Decimal | None] = marks_col()  # what the paper *claims*
    questions_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    questions: Mapped[list["Question"]] = relationship(
        back_populates="exam", cascade="all, delete-orphan", order_by="Question.position"
    )


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[uuid.UUID] = uuid_pk()
    exam_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("exams.id", ondelete="CASCADE"), nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    section: Mapped[str | None] = mapped_column(String(100))
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # For a question with sub-parts this is the programmatic sum of the sub-part marks.
    max_marks: Mapped[Decimal] = marks_col(nullable=False)
    topic: Mapped[str | None] = mapped_column(String(200))
    # Internal choice: questions sharing a group are alternatives; the student answers `choice_count` of them.
    choice_group: Mapped[str | None] = mapped_column(String(50))
    choice_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    exam: Mapped[Exam] = relationship(back_populates="questions")
    subquestions: Mapped[list["Subquestion"]] = relationship(
        back_populates="question", cascade="all, delete-orphan", order_by="Subquestion.position"
    )

    __table_args__ = (
        UniqueConstraint("exam_id", "number", name="uq_questions_exam_number"),
        CheckConstraint("max_marks >= 0", name="max_marks_nonneg"),
        CheckConstraint("choice_count >= 1", name="choice_count_positive"),
    )


class Subquestion(Base):
    __tablename__ = "subquestions"

    id: Mapped[uuid.UUID] = uuid_pk()
    question_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(10), nullable=False)  # a, b, c ... or i, ii
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    max_marks: Mapped[Decimal] = marks_col(nullable=False)
    topic: Mapped[str | None] = mapped_column(String(200))

    question: Mapped[Question] = relationship(back_populates="subquestions")

    __table_args__ = (
        UniqueConstraint("question_id", "label", name="uq_subquestions_question_label"),
        CheckConstraint("max_marks >= 0", name="max_marks_nonneg"),
    )


class Rubric(Base, TimestampMixin):
    """One rubric per exam; the marking scheme itself lives in immutable-once-approved versions."""

    __tablename__ = "rubrics"

    id: Mapped[uuid.UUID] = uuid_pk()
    exam_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("exams.id", ondelete="CASCADE"), nullable=False, unique=True)

    versions: Mapped[list["RubricVersion"]] = relationship(
        back_populates="rubric", cascade="all, delete-orphan", order_by="RubricVersion.version_number"
    )


class RubricVersion(Base, TimestampMixin):
    __tablename__ = "rubric_versions"

    id: Mapped[uuid.UUID] = uuid_pk()
    rubric_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rubrics.id", ondelete="CASCADE"), nullable=False, index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[RubricVersionStatus] = enum_col(RubricVersionStatus, nullable=False, default=RubricVersionStatus.DRAFT)
    source: Mapped[RubricSource] = enum_col(RubricSource, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # set the first time an evaluation is produced with this version
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)

    rubric: Mapped[Rubric] = relationship(back_populates="versions")
    criteria: Mapped[list["RubricCriterion"]] = relationship(
        back_populates="version", cascade="all, delete-orphan", order_by="RubricCriterion.position"
    )

    __table_args__ = (
        UniqueConstraint("rubric_id", "version_number", name="uq_rubric_versions_rubric_number"),
        Index("uq_rubric_versions_one_approved", "rubric_id", unique=True, postgresql_where=sql_text("status = 'APPROVED'")),
        Index("uq_rubric_versions_one_draft", "rubric_id", unique=True, postgresql_where=sql_text("status = 'DRAFT'")),
    )


class RubricCriterion(Base):
    __tablename__ = "rubric_criteria"

    id: Mapped[uuid.UUID] = uuid_pk()
    rubric_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rubric_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    question_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("questions.id"), nullable=False, index=True)
    subquestion_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("subquestions.id"), index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    expected_points: Mapped[str | None] = mapped_column(Text)
    max_marks: Mapped[Decimal] = marks_col(nullable=False)
    # How well-grounded the AI considered this criterion (teacher-authored criteria are 1.0)
    generation_confidence: Mapped[float | None] = mapped_column(Float)

    version: Mapped[RubricVersion] = relationship(back_populates="criteria")

    __table_args__ = (CheckConstraint("max_marks > 0", name="max_marks_positive"),)
