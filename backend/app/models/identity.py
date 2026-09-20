"""Users, sessions, courses, course membership and students."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func, text as sql_text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, SoftDeleteMixin, TimestampMixin, enum_col, uuid_pk
from app.models.enums import CourseRole, UserRole


class User(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[UserRole] = enum_col(UserRole, nullable=False, default=UserRole.TEACHER)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        # emails are unique case-insensitively among live accounts
        Index("uq_users_email_lower", sql_text("lower(email)"), unique=True, postgresql_where=sql_text("deleted_at IS NULL")),
    )


class UserSession(Base):
    """Server-side session. The cookie carries a random token; only its HMAC is stored."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    ip_address: Mapped[str | None] = mapped_column(String(64))


class Course(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "courses"

    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    members: Mapped[list["CourseMember"]] = relationship(back_populates="course", cascade="all, delete-orphan")

    __table_args__ = (
        Index("uq_courses_owner_code", "owner_id", "code", unique=True, postgresql_where=sql_text("deleted_at IS NULL")),
    )


class CourseMember(Base):
    __tablename__ = "course_members"

    id: Mapped[uuid.UUID] = uuid_pk()
    course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    role: Mapped[CourseRole] = enum_col(CourseRole, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    course: Mapped[Course] = relationship(back_populates="members")

    __table_args__ = (UniqueConstraint("course_id", "user_id", name="uq_course_members_course_user"),)


class Student(Base, TimestampMixin, SoftDeleteMixin):
    """A student is scoped to a course; the roll number identifies them within it."""

    __tablename__ = "students"

    id: Mapped[uuid.UUID] = uuid_pk()
    course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True)
    roll_number: Mapped[str] = mapped_column(String(64), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)

    __table_args__ = (
        Index("uq_students_course_roll", "course_id", "roll_number", unique=True, postgresql_where=sql_text("deleted_at IS NULL")),
    )
