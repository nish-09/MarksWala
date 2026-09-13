import os
import uuid
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, BackgroundTasks
from sqlalchemy.orm import Session
from typing import List
from app.db.session import get_db
from app.api import deps
from app.models.user import User
from app.models.exam import Exam
from app.models.question import Question
from app.models.rubric import Rubric, RubricCriterion
from app.services.question_parser import parse_question_paper
from app.services.rubric_engine import generate_rubric
from pydantic import BaseModel

router = APIRouter()
UPLOAD_DIR = "uploads"

class QuestionOut(BaseModel):
    id: int
    question_number: str
    text: str
    marks: float
    
    class Config:
        from_attributes = True

from app.db.session import SessionLocal

def process_question_paper(file_path: str, exam_id: int):
    db = SessionLocal()
    try:
        exam = db.query(Exam).filter(Exam.id == exam_id).first()
        if not exam:
            return
        exam.question_paper_status = "PROCESSING"
        db.commit()

        try:
            questions_data = parse_question_paper(file_path, exam_id)
            if not questions_data:
                raise ValueError("The AI could not extract any questions from this question paper.")

            for q_data in questions_data:
                question = Question(
                    exam_id=exam_id,
                    question_number=q_data["question_number"],
                    text=q_data["text"],
                    marks=q_data["marks"]
                )
                db.add(question)
                db.commit()
                db.refresh(question)

                # Auto-generate rubric
                rubric_criteria = generate_rubric(question.text, question.marks)

                rubric = Rubric(question_id=question.id, is_approved=False)
                db.add(rubric)
                db.commit()
                db.refresh(rubric)

                for crit in rubric_criteria:
                    rc = RubricCriterion(
                        rubric_id=rubric.id,
                        description=crit["description"],
                        marks=crit["marks"],
                        order=crit["order"]
                    )
                    db.add(rc)
                db.commit()

            exam.question_paper_status = "COMPLETED"
            exam.question_paper_error = None
            db.commit()
        except Exception as e:
            print(f"Question paper processing failed for exam {exam_id}: {e}")
            exam.question_paper_status = "FAILED"
            exam.question_paper_error = str(e)[:500]
            db.commit()
    finally:
        db.close()

@router.post("/upload", response_model=dict)
async def upload_question_paper(
    exam_id: int,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_user)
):
    exam = deps.get_owned_exam(exam_id, db, current_user)

    ext = file.filename.split(".")[-1]
    unique_filename = f"qpaper_{uuid.uuid4()}.{ext}"
    file_path = os.path.join(UPLOAD_DIR, unique_filename)

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    with open(file_path, "wb") as buffer:
        content = await file.read()
        buffer.write(content)

    exam.question_paper_status = "PROCESSING"
    exam.question_paper_error = None
    db.commit()

    # Trigger parsing in background to not block the API
    background_tasks.add_task(process_question_paper, file_path, exam_id)

    return {"status": "processing"}

@router.get("/", response_model=List[QuestionOut])
def get_questions(
    exam_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_user)
):
    deps.get_owned_exam(exam_id, db, current_user)
    return db.query(Question).filter(Question.exam_id == exam_id).all()

@router.get("/status", response_model=dict)
def get_question_paper_status(
    exam_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_user)
):
    exam = deps.get_owned_exam(exam_id, db, current_user)
    return {
        "status": exam.question_paper_status,
        "error": exam.question_paper_error,
    }
