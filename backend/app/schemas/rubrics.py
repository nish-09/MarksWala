from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import RubricSource, RubricVersionStatus
from app.schemas.common import ORM, Marks


class CriterionIn(BaseModel):
    id: uuid.UUID | None = None
    title: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=4000)
    expected_points: str | None = Field(default=None, max_length=4000)
    max_marks: float = Field(gt=0, le=1000)


class UnitIn(BaseModel):
    question_id: uuid.UUID
    subquestion_id: uuid.UUID | None = None
    criteria: list[CriterionIn] = Field(max_length=20)


class RubricPut(BaseModel):
    units: list[UnitIn]


class CriterionOut(ORM):
    id: uuid.UUID
    position: int
    title: str
    description: str | None
    expected_points: str | None
    max_marks: Marks
    generation_confidence: float | None


class UnitOut(BaseModel):
    question_id: uuid.UUID
    subquestion_id: uuid.UUID | None
    label: str  # Q1(a)
    question_text: str
    topic: str | None
    max_marks: Marks
    criteria_total: Marks
    balanced: bool
    criteria: list[CriterionOut]


class VersionOut(ORM):
    id: uuid.UUID
    version_number: int
    status: RubricVersionStatus
    source: RubricSource
    approved_at: datetime | None
    locked_at: datetime | None
    created_at: datetime
    evaluations_using: int = 0


class RubricOut(BaseModel):
    exam_id: uuid.UUID
    versions: list[VersionOut]
    version: VersionOut | None  # the version shown
    units: list[UnitOut]
    all_balanced: bool
    editable: bool  # only DRAFT versions are editable
    generation_job_id: uuid.UUID | None = None
