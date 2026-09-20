"""Authorization. Every object is reachable only through its course, and a course only by its members.

Non-members receive 404 (not 403) so the existence of other teachers' data is not revealed.
Rules are enforced here, on the server; the frontend's checks are cosmetic.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Forbidden, NotFound
from app.models import (
    Answer,
    AnswerSheet,
    Course,
    CourseMember,
    Evaluation,
    Exam,
    Resource,
    Student,
    User,
)
from app.models.enums import CourseRole

_RANK = {CourseRole.VIEWER: 1, CourseRole.INSTRUCTOR: 2, CourseRole.OWNER: 3}


def _member_role(db: Session, user: User, course_id: uuid.UUID) -> CourseRole | None:
    return db.scalar(
        select(CourseMember.role).where(CourseMember.course_id == course_id, CourseMember.user_id == user.id)
    )


def get_course(db: Session, user: User, course_id: uuid.UUID, min_role: CourseRole = CourseRole.VIEWER) -> Course:
    course = db.scalar(select(Course).where(Course.id == course_id, Course.deleted_at.is_(None)))
    role = _member_role(db, user, course_id) if course else None
    if course is None or role is None:
        raise NotFound("Course not found.")
    if _RANK[role] < _RANK[min_role]:
        raise Forbidden("You do not have permission to do that in this course.")
    return course


def course_ids_for(db: Session, user: User) -> list[uuid.UUID]:
    return list(db.scalars(select(CourseMember.course_id).where(CourseMember.user_id == user.id)))


def get_exam(db: Session, user: User, exam_id: uuid.UUID, min_role: CourseRole = CourseRole.VIEWER) -> Exam:
    exam = db.scalar(select(Exam).where(Exam.id == exam_id, Exam.deleted_at.is_(None)))
    if exam is None:
        raise NotFound("Exam not found.")
    get_course(db, user, exam.course_id, min_role)  # raises NotFound for non-members
    return exam


def get_resource(db: Session, user: User, resource_id: uuid.UUID, min_role: CourseRole = CourseRole.VIEWER) -> Resource:
    res = db.scalar(select(Resource).where(Resource.id == resource_id, Resource.deleted_at.is_(None)))
    if res is None:
        raise NotFound("Resource not found.")
    get_course(db, user, res.course_id, min_role)
    return res


def get_sheet(db: Session, user: User, sheet_id: uuid.UUID, min_role: CourseRole = CourseRole.VIEWER) -> tuple[AnswerSheet, Exam]:
    sheet = db.scalar(select(AnswerSheet).where(AnswerSheet.id == sheet_id, AnswerSheet.deleted_at.is_(None)))
    if sheet is None:
        raise NotFound("Answer sheet not found.")
    exam = get_exam(db, user, sheet.exam_id, min_role)
    return sheet, exam


def get_answer(db: Session, user: User, answer_id: uuid.UUID, min_role: CourseRole = CourseRole.VIEWER) -> tuple[Answer, AnswerSheet, Exam]:
    answer = db.get(Answer, answer_id)
    if answer is None:
        raise NotFound("Answer not found.")
    sheet, exam = get_sheet(db, user, answer.answer_sheet_id, min_role)
    return answer, sheet, exam


def get_evaluation(db: Session, user: User, evaluation_id: uuid.UUID, min_role: CourseRole = CourseRole.VIEWER):
    ev = db.get(Evaluation, evaluation_id)
    if ev is None:
        raise NotFound("Evaluation not found.")
    answer, sheet, exam = get_answer(db, user, ev.answer_id, min_role)
    return ev, answer, sheet, exam


def get_student(db: Session, user: User, student_id: uuid.UUID, min_role: CourseRole = CourseRole.VIEWER) -> Student:
    st = db.scalar(select(Student).where(Student.id == student_id, Student.deleted_at.is_(None)))
    if st is None:
        raise NotFound("Student not found.")
    get_course(db, user, st.course_id, min_role)
    return st
