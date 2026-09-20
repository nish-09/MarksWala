"""Derives the API view of an exam (totals, validation issues, rubric/sheet status) from persisted rows."""
from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import AnswerSheet, Exam, ProcessingJob, Question, Rubric, RubricVersion
from app.models.enums import JobKind, RubricVersionStatus, SheetStatus
from app.schemas.exams import ExamOut, IssueOut, QuestionOut, QuestionsOut
from app.schemas.jobs import JobOut
from app.services.marks import units_of, validate_structure
from app.services.structure import structure_locked


def thresholds(exam: Exam) -> tuple[float, float]:
    """(high, review) — exam overrides fall back to the configured defaults."""
    high = exam.confidence_high_threshold if exam.confidence_high_threshold is not None else settings.confidence_high_threshold
    low = exam.confidence_review_threshold if exam.confidence_review_threshold is not None else settings.confidence_review_threshold
    return high, low


def pass_percentage(exam: Exam) -> Decimal:
    return exam.pass_percentage if exam.pass_percentage is not None else Decimal(str(settings.default_pass_percentage))


def load_questions(db: Session, exam_id: uuid.UUID) -> list[Question]:
    return list(db.scalars(select(Question).where(Question.exam_id == exam_id).order_by(Question.position)))


def exam_total_for(db: Session, exam: Exam) -> Decimal:
    from app.services.marks import exam_total

    return exam_total(load_questions(db, exam.id))


def rubric_status(db: Session, exam_id: uuid.UUID) -> str:
    statuses = set(
        db.scalars(
            select(RubricVersion.status).join(Rubric, Rubric.id == RubricVersion.rubric_id).where(Rubric.exam_id == exam_id)
        )
    )
    if RubricVersionStatus.APPROVED in statuses:
        return "APPROVED"
    if RubricVersionStatus.DRAFT in statuses:
        return "DRAFT"
    return "NONE"


def exam_out(db: Session, exam: Exam) -> ExamOut:
    questions = load_questions(db, exam.id)
    report = validate_structure(questions, exam.declared_total_marks)
    high, low = thresholds(exam)
    sheets = db.scalar(select(func.count()).select_from(AnswerSheet).where(AnswerSheet.exam_id == exam.id, AnswerSheet.deleted_at.is_(None))) or 0
    evaluated = db.scalar(
        select(func.count()).select_from(AnswerSheet).where(
            AnswerSheet.exam_id == exam.id, AnswerSheet.deleted_at.is_(None), AnswerSheet.status == SheetStatus.EVALUATED
        )
    ) or 0
    job = db.scalar(
        select(ProcessingJob)
        .where(ProcessingJob.kind == JobKind.PARSE_QUESTION_PAPER, ProcessingJob.entity_id == exam.id)
        .order_by(ProcessingJob.queued_at.desc())
        .limit(1)
    )
    return ExamOut(
        id=exam.id,
        course_id=exam.course_id,
        title=exam.title,
        paper_status=exam.paper_status,
        paper_filename=exam.paper_filename,
        paper_error=exam.paper_error,
        declared_total_marks=exam.declared_total_marks,
        computed_total_marks=report.computed_total,
        total_matches_declared=report.total_matches_declared,
        questions_confirmed_at=exam.questions_confirmed_at,
        question_count=len(questions),
        unit_count=len(units_of(questions)),
        pass_percentage=float(pass_percentage(exam)),
        confidence_high_threshold=high,
        confidence_review_threshold=low,
        rubric_status=rubric_status(db, exam.id),
        sheet_count=sheets,
        evaluated_count=evaluated,
        created_at=exam.created_at,
        paper_job=JobOut.model_validate(job) if job else None,
        issues=[IssueOut(**i.as_dict()) for i in report.issues],
    )


def questions_out(db: Session, exam: Exam) -> QuestionsOut:
    questions = load_questions(db, exam.id)
    report = validate_structure(questions, exam.declared_total_marks)
    return QuestionsOut(
        questions=[QuestionOut.model_validate(q) for q in questions],
        computed_total_marks=report.computed_total,
        declared_total_marks=exam.declared_total_marks,
        total_matches_declared=report.total_matches_declared,
        issues=[IssueOut(**i.as_dict()) for i in report.issues],
        confirmed=exam.questions_confirmed_at is not None,
        editable_structure=not structure_locked(db, exam),
    )
