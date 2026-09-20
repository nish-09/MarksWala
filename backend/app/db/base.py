"""Declarative base, naming conventions and shared column mixins."""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, MetaData, Numeric, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


def enum_col(enum_cls: type[enum.Enum], **kw):
    """VARCHAR + CHECK constraint (not a native PG enum, so migrations stay simple)."""
    return mapped_column(
        Enum(
            enum_cls,
            native_enum=False,
            length=40,
            create_constraint=True,
            name=f"{enum_cls.__name__.lower()}",
            values_callable=lambda e: [m.value for m in e],
        ),
        **kw,
    )


def marks_col(**kw):
    """Marks are stored as exact decimals with two fractional digits."""
    return mapped_column(Numeric(8, 2), **kw)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class SoftDeleteMixin:
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


__all__ = ["Base", "uuid_pk", "enum_col", "marks_col", "TimestampMixin", "SoftDeleteMixin"]
