from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from app.db.session import get_db
from app.api import deps
from app.models.user import User
from app.models.course import Course
from app.schemas.course import CourseCreate, CourseOut

router = APIRouter()

@router.post("/", response_model=CourseOut)
def create_course(
    course_in: CourseCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_user)
):
    course = Course(
        title=course_in.title,
        description=course_in.description,
        teacher_id=current_user.id
    )
    db.add(course)
    db.commit()
    db.refresh(course)
    return course

@router.get("/", response_model=List[CourseOut])
def read_courses(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_user)
):
    courses = db.query(Course).filter(Course.teacher_id == current_user.id).offset(skip).limit(limit).all()
    return courses
