"""Append-only audit trail."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog


def record(
    db: Session,
    *,
    actor_id: uuid.UUID | None,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | None = None,
    course_id: uuid.UUID | None = None,
    exam_id: uuid.UUID | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    ip: str | None = None,
) -> AuditLog:
    entry = AuditLog(
        actor_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        course_id=course_id,
        exam_id=exam_id,
        before=_jsonable(before),
        after=_jsonable(after),
        ip_address=ip,
    )
    db.add(entry)
    return entry


def _jsonable(d: dict[str, Any] | None) -> dict[str, Any] | None:
    if d is None:
        return None
    import json
    from decimal import Decimal

    def default(o: Any):
        if isinstance(o, Decimal):
            return float(o)
        return str(o)

    return json.loads(json.dumps(d, default=default))
