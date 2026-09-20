from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import FlagKind, FlagSeverity, MappingMethod, SheetStatus
from app.schemas.common import ORM, Marks
from app.schemas.jobs import JobOut


class StudentOut(ORM):
    id: uuid.UUID
    roll_number: str
    full_name: str


class SheetOut(ORM):
    id: uuid.UUID
    exam_id: uuid.UUID
    student: StudentOut | None = None
    original_filename: str
    page_count: int | None
    size_bytes: int
    status: SheetStatus
    detected_name: str | None
    detected_roll_number: str | None
    identity_confidence: float | None
    error_message: str | None
    created_at: datetime
    processed_at: datetime | None
    job: JobOut | None = None
    open_flags: int = 0
    answer_count: int = 0
    missing_count: int = 0
    evaluated_count: int = 0


class UploadItem(BaseModel):
    filename: str
    ok: bool
    error_code: str | None = None
    error_message: str | None = None
    sheet: SheetOut | None = None


class UploadResponse(BaseModel):
    batch_id: uuid.UUID
    results: list[UploadItem]


class MappingOut(ORM):
    id: uuid.UUID
    answer_id: uuid.UUID | None
    page_number: int
    detected_label: str | None
    text: str
    confidence: float
    method: MappingMethod
    is_active: bool


class PageOut(BaseModel):
    page_number: int
    width: int
    height: int
    image_url: str
    ocr_confidence: float | None
    has_diagram: bool
    unreadable_spans: list[str]
    ocr_text: str  # the original OCR — immutable


class AnswerOut(BaseModel):
    id: uuid.UUID
    question_id: uuid.UUID
    subquestion_id: uuid.UUID | None
    label: str
    question_text: str
    max_marks: Marks
    original_text: str
    corrected_text: str | None
    effective_text: str
    is_missing: bool
    has_diagram: bool
    ocr_confidence: float | None
    mapping_confidence: float | None
    corrected_at: datetime | None
    pages: list[int]
    mappings: list[MappingOut]


class FlagOut(ORM):
    id: uuid.UUID
    answer_id: uuid.UUID | None
    evaluation_id: uuid.UUID | None
    kind: FlagKind
    severity: FlagSeverity
    detail: str
    value: float | None
    threshold: float | None
    resolved_at: datetime | None


class SheetDetailOut(SheetOut):
    pages: list[PageOut]
    answers: list[AnswerOut]
    unassigned: list[MappingOut]
    flags: list[FlagOut]


class StudentAssignIn(BaseModel):
    roll_number: str = Field(min_length=1, max_length=64)
    full_name: str | None = Field(default=None, max_length=200)


class ReassignIn(BaseModel):
    answer_id: uuid.UUID | None  # null = move back to "unassigned"


class CorrectedTextIn(BaseModel):
    text: str | None = Field(max_length=60000)  # null reverts to the original OCR


class StudentSummary(StudentOut):
    sheet_count: int
