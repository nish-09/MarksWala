from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from app.db.session import get_db
from app.api import deps
from app.models.user import User
from app.models.exam import Exam
from app.models.course import Course
from app.schemas.exam import ExamCreate, ExamOut

router = APIRouter()

@router.post("/", response_model=ExamOut)
def create_exam(
    exam_in: ExamCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_user)
):
    course = db.query(Course).filter(Course.id == exam_in.course_id, Course.teacher_id == current_user.id).first()
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
        
    exam = Exam(
        title=exam_in.title,
        course_id=exam_in.course_id
    )
    db.add(exam)
    db.commit()
    db.refresh(exam)
    return exam

@router.get("/", response_model=List[ExamOut])
def read_exams(
    course_id: int,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_user)
):
    course = db.query(Course).filter(Course.id == course_id, Course.teacher_id == current_user.id).first()
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
        
    exams = db.query(Exam).filter(Exam.course_id == course_id).offset(skip).limit(limit).all()
    return exams
