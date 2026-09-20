from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.enums import EvaluationStatus, FlagKind, FlagSeverity, ReviewDecision, ReviewStatus, TextSource
from app.schemas.common import Marks
from app.schemas.sheets import AnswerOut, FlagOut, PageOut, StudentOut


class ConfidenceOut(BaseModel):
    ocr: float | None
    mapping: float | None
    retrieval: float | None
    rubric: float | None
    evaluation: float | None
    overall: float | None
    band: str  # high | review_recommended | mandatory_review


class OverrideOut(BaseModel):
    id: uuid.UUID
    ai_score: Marks
    teacher_score: Marks
    final_score: Marks
    reason: str
    teacher_id: uuid.UUID
    teacher_name: str | None
    is_active: bool
    created_at: datetime


class CriterionEvalOut(BaseModel):
    id: uuid.UUID  # evaluation_criterion_id
    rubric_criterion_id: uuid.UUID
    position: int
    title: str
    description: str | None
    expected_points: str | None
    max_score: Marks
    ai_score: Marks
    final_score: Marks
    satisfied: bool
    partial: bool
    evidence: str | None
    missing_points: str | None
    feedback: str | None
    confidence: float
    override: OverrideOut | None
    override_history: list[OverrideOut]


class SourceOut(BaseModel):
    chunk_id: uuid.UUID | None
    resource_id: uuid.UUID | None
    resource_title: str
    page_number: int | None
    slide_number: int | None
    section: str | None
    rank: int
    score: float
    text: str


class ReviewStateOut(BaseModel):
    id: uuid.UUID | None
    status: ReviewStatus | None
    mandatory: bool
    decision: ReviewDecision | None
    reviewer_name: str | None
    notes: str | None
    resolved_at: datetime | None


class EvalHistoryOut(BaseModel):
    id: uuid.UUID
    attempt: int
    is_current: bool
    ai_total: Marks
    text_source: TextSource
    model: str
    created_at: datetime


class EvaluationDetailOut(BaseModel):
    id: uuid.UUID
    attempt: int
    status: EvaluationStatus
    text_source: TextSource
    answer_text_used: str
    provider: str
    model: str
    prompt_version: str
    rubric_version_id: uuid.UUID
    rubric_version_number: int
    ai_total: Marks
    final_total: Marks
    max_total: Marks
    overall_feedback: str | None
    error_message: str | None
    confidence: ConfidenceOut
    criteria: list[CriterionEvalOut]
    sources: list[SourceOut]
    flags: list[FlagOut]
    review: ReviewStateOut
    stale: bool  # the answer text changed after this evaluation ran
    created_at: datetime


class ReviewDetailOut(BaseModel):
    exam_id: uuid.UUID
    exam_title: str
    sheet_id: uuid.UUID
    student: StudentOut | None
    question_label: str
    question_text: str
    question_stem: str | None
    max_marks: Marks
    answer: AnswerOut
    pages: list[PageOut]
    evaluation: EvaluationDetailOut | None
    history: list[EvalHistoryOut]
    can_evaluate: bool  # an approved rubric exists
    next_answer_id: uuid.UUID | None
    prev_answer_id: uuid.UUID | None


class QueueItemOut(BaseModel):
    kind: str  # EVALUATION | SHEET
    review_id: uuid.UUID | None
    evaluation_id: uuid.UUID | None
    answer_id: uuid.UUID | None
    sheet_id: uuid.UUID
    student: StudentOut | None
    sheet_filename: str
    question_label: str | None
    max_marks: Marks | None
    ai_total: Marks | None
    final_total: Marks | None
    overall_confidence: float | None
    band: str | None
    mandatory: bool
    status: ReviewStatus | None
    decision: ReviewDecision | None
    stale: bool
    reasons: list[str]
    flag_kinds: list[FlagKind]
    severities: list[FlagSeverity]


class QueueOut(BaseModel):
    items: list[QueueItemOut]
    pending_mandatory: int
    pending_recommended: int
    resolved: int
