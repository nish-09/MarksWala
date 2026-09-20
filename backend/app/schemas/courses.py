from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.models.enums import CourseRole
from app.schemas.common import ORM


def _clean(v: str) -> str:
    v = v.strip()
    if not v:
        raise ValueError("cannot be blank")
    return v


class CourseCreate(BaseModel):
    code: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)

    _c = field_validator("code", "name")(_clean)


class CourseUpdate(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=50)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)

    @field_validator("code", "name")
    @classmethod
    def _c(cls, v):
        return None if v is None else _clean(v)


class CourseOut(ORM):
    id: uuid.UUID
    code: str
    name: str
    description: str | None
    owner_id: uuid.UUID
    my_role: CourseRole
    created_at: datetime


class CourseSummary(CourseOut):
    resource_count: int
    resources_ready: int
    exam_count: int
    student_count: int


class MemberIn(BaseModel):
    email: EmailStr
    role: CourseRole = CourseRole.INSTRUCTOR


class MemberOut(ORM):
    user_id: uuid.UUID
    email: str
    full_name: str
    role: CourseRole
