from __future__ import annotations

import uuid
from datetime import datetime

from app.models.enums import JobKind, JobStatus
from app.schemas.common import ORM


class JobOut(ORM):
    id: uuid.UUID
    kind: JobKind
    entity_type: str
    entity_id: uuid.UUID
    course_id: uuid.UUID | None
    exam_id: uuid.UUID | None
    status: JobStatus
    progress: float | None
    progress_message: str | None
    attempts: int
    max_attempts: int
    error_code: str | None
    error_message: str | None
    queued_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    entity_label: str | None = None
