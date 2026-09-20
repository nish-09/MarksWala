from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from app.api.deps import DB, CurrentUser, client_ip
from app.core.errors import Conflict, NotFound, Unprocessable
from app.models import Course, CourseMember, Exam, Resource, Student, User
from app.models.enums import CourseRole, ResourceStatus
from app.schemas.common import Message
from app.schemas.courses import (
    CourseCreate,
    CourseOut,
    CourseSummary,
    CourseUpdate,
    MemberIn,
    MemberOut,
)
from app.services import access, audit

router = APIRouter(prefix="/courses", tags=["courses"])


def _role(db, user, course_id) -> CourseRole:
    return db.scalar(select(CourseMember.role).where(CourseMember.course_id == course_id, CourseMember.user_id == user.id))


def _out(db, user, c: Course, cls=CourseOut, **extra):
    return cls(
        id=c.id, code=c.code, name=c.name, description=c.description, owner_id=c.owner_id,
        my_role=_role(db, user, c.id), created_at=c.created_at, **extra,
    )


def _summary(db, user, c: Course) -> CourseSummary:
    counts = dict(
        resource_count=db.scalar(select(func.count()).select_from(Resource).where(Resource.course_id == c.id, Resource.deleted_at.is_(None))) or 0,
        resources_ready=db.scalar(
            select(func.count()).select_from(Resource).where(
                Resource.course_id == c.id, Resource.deleted_at.is_(None), Resource.status == ResourceStatus.COMPLETED
            )
        ) or 0,
        exam_count=db.scalar(select(func.count()).select_from(Exam).where(Exam.course_id == c.id, Exam.deleted_at.is_(None))) or 0,
        student_count=db.scalar(select(func.count()).select_from(Student).where(Student.course_id == c.id, Student.deleted_at.is_(None))) or 0,
    )
    return _out(db, user, c, CourseSummary, **counts)


@router.get("", response_model=list[CourseSummary])
def list_courses(db: DB, user: CurrentUser):
    rows = db.scalars(
        select(Course)
        .join(CourseMember, CourseMember.course_id == Course.id)
        .where(CourseMember.user_id == user.id, Course.deleted_at.is_(None))
        .order_by(Course.created_at.desc())
    ).all()
    return [_summary(db, user, c) for c in rows]


@router.post("", response_model=CourseSummary, status_code=201)
def create_course(body: CourseCreate, request: Request, db: DB, user: CurrentUser):
    dup = db.scalar(select(Course.id).where(Course.owner_id == user.id, func.lower(Course.code) == body.code.lower(), Course.deleted_at.is_(None)))
    if dup:
        raise Conflict(f"You already have a course with code {body.code}.", code="course_code_taken")
    course = Course(owner_id=user.id, code=body.code, name=body.name, description=body.description)
    db.add(course)
    db.flush()
    db.add(CourseMember(course_id=course.id, user_id=user.id, role=CourseRole.OWNER))
    audit.record(db, actor_id=user.id, action="course.create", entity_type="course", entity_id=course.id, course_id=course.id,
                 after={"code": course.code, "name": course.name}, ip=client_ip(request))
    db.commit()
    return _summary(db, user, course)


@router.get("/{course_id}", response_model=CourseSummary)
def get_course(course_id: uuid.UUID, db: DB, user: CurrentUser):
    return _summary(db, user, access.get_course(db, user, course_id))


@router.patch("/{course_id}", response_model=CourseSummary)
def update_course(course_id: uuid.UUID, body: CourseUpdate, request: Request, db: DB, user: CurrentUser):
    course = access.get_course(db, user, course_id, CourseRole.INSTRUCTOR)
    before = {"code": course.code, "name": course.name, "description": course.description}
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(course, field, value)
    audit.record(db, actor_id=user.id, action="course.update", entity_type="course", entity_id=course.id, course_id=course.id,
                 before=before, after=body.model_dump(exclude_unset=True), ip=client_ip(request))
    db.commit()
    return _summary(db, user, course)


@router.delete("/{course_id}", response_model=Message)
def delete_course(course_id: uuid.UUID, request: Request, db: DB, user: CurrentUser):
    course = access.get_course(db, user, course_id, CourseRole.OWNER)
    course.deleted_at = datetime.now(timezone.utc)
    audit.record(db, actor_id=user.id, action="course.delete", entity_type="course", entity_id=course.id, course_id=course.id, ip=client_ip(request))
    db.commit()
    return Message(message="Course deleted.")


@router.get("/{course_id}/members", response_model=list[MemberOut])
def list_members(course_id: uuid.UUID, db: DB, user: CurrentUser):
    access.get_course(db, user, course_id)
    rows = db.execute(
        select(CourseMember.user_id, User.email, User.full_name, CourseMember.role)
        .join(User, User.id == CourseMember.user_id)
        .where(CourseMember.course_id == course_id)
        .order_by(User.full_name)
    ).all()
    return [MemberOut(user_id=r[0], email=r[1], full_name=r[2], role=r[3]) for r in rows]


@router.post("/{course_id}/members", response_model=MemberOut, status_code=201)
def add_member(course_id: uuid.UUID, body: MemberIn, request: Request, db: DB, user: CurrentUser):
    course = access.get_course(db, user, course_id, CourseRole.OWNER)
    if body.role == CourseRole.OWNER:
        raise Unprocessable("A course has exactly one owner.", code="invalid_role")
    target = db.scalar(select(User).where(func.lower(User.email) == body.email.lower(), User.deleted_at.is_(None)))
    if target is None:
        raise NotFound("No MarksWala account exists for that email. Ask them to register first.")
    if db.scalar(select(CourseMember.id).where(CourseMember.course_id == course.id, CourseMember.user_id == target.id)):
        raise Conflict("That person is already a member of this course.", code="already_member")
    db.add(CourseMember(course_id=course.id, user_id=target.id, role=body.role))
    audit.record(db, actor_id=user.id, action="course.member_add", entity_type="course", entity_id=course.id, course_id=course.id,
                 after={"user_id": str(target.id), "role": body.role.value}, ip=client_ip(request))
    db.commit()
    return MemberOut(user_id=target.id, email=target.email, full_name=target.full_name, role=body.role)


@router.delete("/{course_id}/members/{user_id}", response_model=Message)
def remove_member(course_id: uuid.UUID, user_id: uuid.UUID, request: Request, db: DB, user: CurrentUser):
    course = access.get_course(db, user, course_id, CourseRole.OWNER)
    m = db.scalar(select(CourseMember).where(CourseMember.course_id == course.id, CourseMember.user_id == user_id))
    if m is None:
        raise NotFound("Member not found.")
    if m.role == CourseRole.OWNER:
        raise Unprocessable("The course owner cannot be removed.", code="cannot_remove_owner")
    db.delete(m)
    audit.record(db, actor_id=user.id, action="course.member_remove", entity_type="course", entity_id=course.id, course_id=course.id,
                 after={"user_id": str(user_id)}, ip=client_ip(request))
    db.commit()
    return Message(message="Member removed.")
