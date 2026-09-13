from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.api import deps
from app.models.user import User
from app.models.evaluation import Evaluation, Answer, AnswerSheet, Student
from app.models.exam import Exam
import pandas as pd
import os

router = APIRouter()

@router.get("/export/{exam_id}")
def export_results(exam_id: int, db: Session = Depends(get_db)):
    exam = db.query(Exam).filter(Exam.id == exam_id).first()
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
        
    answers = db.query(Answer).join(AnswerSheet).filter(AnswerSheet.exam_id == exam_id).all()
    
    data = []
    for ans in answers:
        if ans.evaluation:
            data.append({
                "Student Name": ans.answer_sheet.student.name,
                "Roll Number": ans.answer_sheet.student.roll_number,
                "Question": ans.question.question_number,
                "Max Marks": ans.question.marks,
                "Score": ans.evaluation.total_score,
                "Feedback": ans.evaluation.feedback,
                "Teacher Overridden": ans.evaluation.teacher_override,
                "AI Confidence": ans.evaluation.evaluation_confidence
            })
            
    if not data:
        raise HTTPException(status_code=400, detail="No evaluations available for this exam")
        
    df = pd.DataFrame(data)
    
    export_dir = "uploads/exports"
    os.makedirs(export_dir, exist_ok=True)
    file_path = os.path.join(export_dir, f"exam_{exam_id}_results.xlsx")
    
    df.to_excel(file_path, index=False)
    
    return FileResponse(path=file_path, filename=f"{exam.title}_Results.xlsx")
