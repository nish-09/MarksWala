from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import ResourceStatus
from app.schemas.common import ORM
from app.schemas.jobs import JobOut


class ResourceFileOut(ORM):
    id: uuid.UUID
    original_filename: str
    mime_type: str
    size_bytes: int
    page_count: int | None


class ResourceOut(ORM):
    id: uuid.UUID
    course_id: uuid.UUID
    title: str
    kind: str
    status: ResourceStatus
    chunk_count: int
    error_message: str | None
    processed_at: datetime | None
    created_at: datetime
    files: list[ResourceFileOut]
    job: JobOut | None = None


class ChunkOut(ORM):
    id: uuid.UUID
    chunk_index: int
    page_number: int | None
    slide_number: int | None
    section: str | None
    text: str
    token_count: int


class SearchIn(BaseModel):
    query: str = Field(min_length=2, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20)


class SearchHit(BaseModel):
    chunk_id: uuid.UUID
    resource_id: uuid.UUID
    resource_title: str
    page_number: int | None
    slide_number: int | None
    section: str | None
    score: float
    text: str
