from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.enums import ResultStatus
from app.schemas.common import Marks
from app.schemas.jobs import JobOut
from app.schemas.sheets import StudentOut


class QuestionMarkOut(BaseModel):
    label: str
    topic: str | None
    score: Marks
    max_marks: Marks
    counted: bool
    evaluated: bool
    overridden: bool


class ResultRowOut(BaseModel):
    sheet_id: uuid.UUID
    student: StudentOut | None
    filename: str
    total: Marks
    max_total: Marks
    percentage: Marks
    passed: bool | None
    status: ResultStatus
    pending_reviews: int
    unevaluated: int
    questions: list[QuestionMarkOut]


class ResultsOut(BaseModel):
    exam_id: uuid.UUID
    exam_title: str
    max_total: Marks
    pass_percentage: float
    rows: list[ResultRowOut]


class CriterionResultOut(BaseModel):
    title: str
    max_score: Marks
    ai_score: Marks
    final_score: Marks
    overridden: bool
    missing_points: str | None
    feedback: str | None


class UnitResultOut(QuestionMarkOut):
    answer_id: uuid.UUID | None
    question_text: str
    ai_score: Marks | None
    criteria: list[CriterionResultOut]


class TopicScoreOut(BaseModel):
    topic: str
    score: Marks
    max_score: Marks
    percentage: float


class SheetResultOut(ResultRowOut):
    units: list[UnitResultOut]
    topics: list[TopicScoreOut]
    feedback: dict | None
    feedback_generated_at: datetime | None
    feedback_stale: bool
    feedback_job: JobOut | None = None


class StudentExamOut(BaseModel):
    exam_id: uuid.UUID
    exam_title: str
    sheet_id: uuid.UUID
    total: Marks
    max_total: Marks
    percentage: Marks
    status: ResultStatus
    weak_topics: list[str]


class StudentProfileOut(BaseModel):
    student: StudentOut
    course_id: uuid.UUID
    exams: list[StudentExamOut]
    repeated_weak_topics: list[str]
