"""Shared schema primitives. The API is snake_case end to end."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, PlainSerializer

# Marks are exact Decimals in the database and plain JSON numbers on the wire.
Marks = Annotated[Decimal, PlainSerializer(lambda v: float(v), return_type=float, when_used="json")]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class IdOut(ORM):
    id: uuid.UUID


class Message(BaseModel):
    message: str


class Page[T](BaseModel):
    items: list[T]
    total: int


__all__ = ["Marks", "ORM", "IdOut", "Message", "Page", "datetime", "uuid"]
