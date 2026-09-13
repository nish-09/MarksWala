import os
import uuid
import datetime
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, BackgroundTasks
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from typing import List
from app.db.session import get_db
from app.api import deps
from app.models.user import User
from app.models.evaluation import Student, AnswerSheet, Answer, Evaluation, CriterionScore
from app.models.question import Question
from app.models.rubric import Rubric
from app.models.exam import Exam
from app.services.ocr_engine import pdf_to_images, extract_text_and_map, extract_student_info
from app.services.evaluator import evaluate_answer

router = APIRouter()
UPLOAD_DIR = "uploads"
IMAGES_DIR = "uploads/images"

from app.db.session import SessionLocal

def process_answer_sheet(file_path: str, exam_id: int, student_id: int, answer_sheet_id: int):
    db = SessionLocal()
    try:
        answer_sheet = db.query(AnswerSheet).filter(AnswerSheet.id == answer_sheet_id).first()
        if not answer_sheet:
            return

        try:
            exam = db.query(Exam).filter(Exam.id == exam_id).first()
            if not exam:
                raise ValueError("Exam no longer exists.")
            questions = db.query(Question).filter(Question.exam_id == exam_id).all()
            if not questions:
                raise ValueError("This exam has no approved questions to map answers against yet.")

            q_list = [{"question_number": q.question_number, "text": q.text} for q in questions]

            # 1. Convert PDF to images
            image_paths = pdf_to_images(file_path, os.path.join(IMAGES_DIR, f"ans_{answer_sheet_id}"))
            if not image_paths:
                raise ValueError("Could not extract any pages from this PDF.")

            # 1b. Identify the student from the cover page, best-effort.
            student_info = extract_student_info(image_paths[0])
            student = db.query(Student).filter(Student.id == student_id).first()
            if student:
                if student_info.get("name"):
                    student.name = student_info["name"]
                if student_info.get("roll_number"):
                    student.roll_number = student_info["roll_number"]
                db.commit()

            unmatched_pages = 0
            for img_path in image_paths:
                # 2. OCR and Map - a page can contain answers to several questions
                segments = extract_text_and_map(img_path, q_list)
                if not segments:
                    unmatched_pages += 1
                    continue

                for ocr_result in segments:
                    q_num = ocr_result["question_number"]
                    question = next((q for q in questions if q.question_number == q_num), None)

                    if not question:
                        unmatched_pages += 1
                        continue

                    # 3. Save Answer mapping. If this question already has an
                    # answer from a previous page (the answer continues across
                    # pages), append to it instead of creating a duplicate row -
                    # otherwise evaluation would run twice over split content.
                    answer = (
                        db.query(Answer)
                        .filter(Answer.answer_sheet_id == answer_sheet_id, Answer.question_id == question.id)
                        .first()
                    )
                    new_text = ocr_result.get("ocr_text", "")
                    if answer:
                        answer.ocr_text = f"{answer.ocr_text}\n{new_text}".strip()
                        answer.ocr_confidence = min(answer.ocr_confidence or 1.0, ocr_result.get("ocr_confidence", 0.0))
                        answer.mapping_confidence = min(answer.mapping_confidence or 1.0, ocr_result.get("mapping_confidence", 0.0))
                    else:
                        answer = Answer(
                            answer_sheet_id=answer_sheet_id,
                            question_id=question.id,
                            ocr_text=new_text,
                            page_image_path=img_path,
                            ocr_confidence=ocr_result.get("ocr_confidence", 0.0),
                            mapping_confidence=ocr_result.get("mapping_confidence", 0.0)
                        )
                        db.add(answer)
                    db.commit()
                    db.refresh(answer)

            # 4. Evaluate every mapped answer for this sheet now that all pages
            # (and any multi-page answers) have been fully assembled.
            sheet_answers = db.query(Answer).filter(Answer.answer_sheet_id == answer_sheet_id).all()
            for answer in sheet_answers:
                if answer.evaluation:
                    continue
                question = answer.question
                rubric = db.query(Rubric).filter(Rubric.question_id == question.id).first()
                if not (rubric and rubric.is_approved):
                    continue

                eval_result = evaluate_answer(answer.ocr_text, question, rubric, exam.course_id)
                if not eval_result:
                    # The AI call failed (e.g. rate limit/timeout). Never drop the
                    # answer silently - surface it in the review queue at 0 with
                    # confidence 0 so a teacher knows it still needs scoring.
                    evaluation = Evaluation(
                        answer_id=answer.id,
                        total_score=0.0,
                        ai_score=None,
                        feedback="AI evaluation failed (e.g. API error or rate limit). Please score this answer manually.",
                        evaluation_confidence=0.0
                    )
                    db.add(evaluation)
                    db.commit()
                    continue

                evaluation = Evaluation(
                    answer_id=answer.id,
                    total_score=eval_result.get("total_score", 0.0),
                    ai_score=eval_result.get("total_score", 0.0),
                    feedback=eval_result.get("feedback", ""),
                    evaluation_confidence=eval_result.get("evaluation_confidence", 0.0)
                )
                db.add(evaluation)
                db.commit()
                db.refresh(evaluation)

                for ce in eval_result.get("criteria_evaluation", []):
                    db.add(CriterionScore(
                        evaluation_id=evaluation.id,
                        criterion_id=ce["criterion_id"],
                        score=ce["score"],
                        evidence=ce["evidence"]
                    ))
                db.commit()

            answer_sheet.status = "COMPLETED"
            answer_sheet.error_message = (
                f"{unmatched_pages} page(s) could not be mapped to a question and were skipped."
                if unmatched_pages else None
            )
            db.commit()
        except Exception as e:
            print(f"Answer sheet {answer_sheet_id} processing failed: {e}")
            answer_sheet.status = "FAILED"
            answer_sheet.error_message = str(e)[:500]
            db.commit()
    finally:
        db.close()

@router.post("/upload", response_model=dict)
async def upload_answer_sheet(
    exam_id: int,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_user)
):
    deps.get_owned_exam(exam_id, db, current_user)

    # Student identity is unknown until the first page is OCR'd (process_answer_sheet
    # updates name/roll_number once extracted). Placeholder is unique per upload so
    # concurrent uploads never collide before that resolution happens.
    student = Student(name=f"Unidentified Student ({uuid.uuid4().hex[:6]})", roll_number=None)
    db.add(student)
    db.commit()
    db.refresh(student)
    
    ext = file.filename.split(".")[-1]
    unique_filename = f"ans_{uuid.uuid4()}.{ext}"
    file_path = os.path.join(UPLOAD_DIR, unique_filename)
    
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    with open(file_path, "wb") as buffer:
        content = await file.read()
        buffer.write(content)
        
    answer_sheet = AnswerSheet(
        exam_id=exam_id,
        student_id=student.id,
        file_path=file_path
    )
    db.add(answer_sheet)
    db.commit()
    db.refresh(answer_sheet)
    
    background_tasks.add_task(process_answer_sheet, file_path, exam_id, student.id, answer_sheet.id)
    
    return {"status": "processing", "answer_sheet_id": answer_sheet.id}

@router.get("/sheets", response_model=List[dict])
def list_answer_sheets(
    exam_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_user)
):
    """List uploaded answer sheets for an exam with their processing status,
    so the frontend can show real progress/failure instead of guessing from
    the review queue (which only ever shows fully-evaluated answers)."""
    deps.get_owned_exam(exam_id, db, current_user)
    sheets = db.query(AnswerSheet).filter(AnswerSheet.exam_id == exam_id).all()
    return [
        {
            "answer_sheet_id": s.id,
            "student_name": s.student.name,
            "roll_number": s.student.roll_number,
            "status": s.status,
            "error_message": s.error_message,
            "answers_mapped": len(s.answers),
            "uploaded_at": s.uploaded_at,
        }
        for s in sheets
    ]

@router.get("/{answer_id}/image")
def get_answer_image(
    answer_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_user)
):
    """Serve the original scanned page image for an answer, so the review
    screen can show the actual handwriting instead of a text placeholder."""
    answer = deps.get_owned_answer(answer_id, db, current_user)
    if not answer.page_image_path or not os.path.exists(answer.page_image_path):
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(path=answer.page_image_path, media_type="image/png")

@router.put("/{answer_id}/correct-ocr", response_model=dict)
def correct_ocr(
    answer_id: int,
    corrected_text: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_user)
):
    """Store a teacher's corrected transcription without ever touching the original OCR text."""
    answer = deps.get_owned_answer(answer_id, db, current_user)

    answer.corrected_text = corrected_text
    db.commit()
    return {"status": "success", "ocr_text": answer.ocr_text, "corrected_text": answer.corrected_text}

@router.get("/review-queue", response_model=List[dict])
def get_review_queue(
    exam_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_user)
):
    deps.get_owned_exam(exam_id, db, current_user)
    answers = db.query(Answer).join(AnswerSheet).filter(AnswerSheet.exam_id == exam_id).all()
    queue = []
    for ans in answers:
        if not ans.evaluation:
            continue
        low_mapping_confidence = (ans.mapping_confidence or 0.0) < 0.5
        queue.append({
            "answer_id": ans.id,
            "question_number": ans.question.question_number,
            "student_name": ans.answer_sheet.student.name,
            "roll_number": ans.answer_sheet.student.roll_number,
            "ocr_text": ans.ocr_text,
            "corrected_text": ans.corrected_text,
            "image_path": ans.page_image_path,
            "mapping_confidence": ans.mapping_confidence,
            "ocr_confidence": ans.ocr_confidence,
            "requires_mapping_review": low_mapping_confidence,
            "total_score": ans.evaluation.total_score,
            "ai_score": ans.evaluation.ai_score,
            "feedback": ans.evaluation.feedback,
            "confidence": ans.evaluation.evaluation_confidence,
            "is_reviewed": ans.evaluation.is_reviewed,
            "teacher_override": ans.evaluation.teacher_override,
            "override_reason": ans.evaluation.override_reason,
            "max_marks": ans.question.marks
        })
    return queue

@router.put("/override/{answer_id}", response_model=dict)
def override_evaluation(
    answer_id: int,
    new_score: float,
    feedback: str,
    reason: str = "",
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_user)
):
    answer = deps.get_owned_answer(answer_id, db, current_user)

    evaluation = db.query(Evaluation).filter(Evaluation.answer_id == answer_id).first()
    if not evaluation:
        raise HTTPException(status_code=404, detail="Evaluation not found")

    max_marks = answer.question.marks if answer and answer.question else new_score
    if new_score < 0 or new_score > max_marks:
        raise HTTPException(status_code=400, detail=f"Score must be between 0 and {max_marks}.")

    # The original AI score is preserved forever, never overwritten.
    if evaluation.ai_score is None:
        evaluation.ai_score = evaluation.total_score

    evaluation.total_score = new_score
    evaluation.feedback = feedback
    evaluation.teacher_override = True
    evaluation.override_reason = reason
    evaluation.is_reviewed = True
    evaluation.reviewed_by = current_user.id
    evaluation.reviewed_at = datetime.datetime.utcnow()

    db.commit()
    return {"status": "success"}
