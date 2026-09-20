from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.enums import PaperStatus
from app.schemas.common import ORM, Marks
from app.schemas.jobs import JobOut


class ExamCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    pass_percentage: float | None = Field(default=None, ge=0, le=100)
    confidence_high_threshold: float | None = Field(default=None, ge=0, le=1)
    confidence_review_threshold: float | None = Field(default=None, ge=0, le=1)

    @field_validator("title")
    @classmethod
    def _t(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Title cannot be blank")
        return v.strip()

    @model_validator(mode="after")
    def _thresholds(self):
        hi, lo = self.confidence_high_threshold, self.confidence_review_threshold
        if hi is not None and lo is not None and lo > hi:
            raise ValueError("The mandatory-review threshold cannot exceed the high-confidence threshold")
        return self


class ExamUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    pass_percentage: float | None = Field(default=None, ge=0, le=100)
    confidence_high_threshold: float | None = Field(default=None, ge=0, le=1)
    confidence_review_threshold: float | None = Field(default=None, ge=0, le=1)


class IssueOut(BaseModel):
    level: str
    message: str
    question_number: int | None = None


class ExamOut(ORM):
    id: uuid.UUID
    course_id: uuid.UUID
    title: str
    paper_status: PaperStatus
    paper_filename: str | None
    paper_error: str | None
    declared_total_marks: Marks | None
    computed_total_marks: Marks
    total_matches_declared: bool | None
    questions_confirmed_at: datetime | None
    question_count: int
    unit_count: int
    pass_percentage: float
    confidence_high_threshold: float
    confidence_review_threshold: float
    rubric_status: str  # NONE | DRAFT | APPROVED
    sheet_count: int
    evaluated_count: int
    created_at: datetime
    paper_job: JobOut | None = None
    issues: list[IssueOut] = []


class SubquestionOut(ORM):
    id: uuid.UUID
    label: str
    position: int
    text: str
    max_marks: Marks
    topic: str | None


class QuestionOut(ORM):
    id: uuid.UUID
    number: int
    position: int
    section: str | None
    text: str
    max_marks: Marks
    topic: str | None
    choice_group: str | None
    choice_count: int
    subquestions: list[SubquestionOut]


class SubquestionIn(BaseModel):
    id: uuid.UUID | None = None
    label: str = Field(min_length=1, max_length=10)
    text: str = Field(min_length=1, max_length=8000)
    max_marks: float = Field(ge=0, le=1000)
    topic: str | None = Field(default=None, max_length=200)


class QuestionIn(BaseModel):
    id: uuid.UUID | None = None
    number: int = Field(ge=1, le=500)
    section: str | None = Field(default=None, max_length=100)
    text: str = Field(default="", max_length=8000)
    max_marks: float = Field(default=0, ge=0, le=1000)  # ignored when subquestions are present (their sum is used)
    topic: str | None = Field(default=None, max_length=200)
    choice_group: str | None = Field(default=None, max_length=50)
    choice_count: int = Field(default=1, ge=1, le=20)
    subquestions: list[SubquestionIn] = []


class QuestionsPut(BaseModel):
    questions: list[QuestionIn] = Field(max_length=500)


class QuestionsOut(BaseModel):
    questions: list[QuestionOut]
    computed_total_marks: Marks
    declared_total_marks: Marks | None
    total_matches_declared: bool | None
    issues: list[IssueOut]
    confirmed: bool
    editable_structure: bool  # false once a rubric is approved or answer sheets exist (only text/topic may change)
