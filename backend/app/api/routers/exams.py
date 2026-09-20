from __future__ import annotations

import uuid
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.api.deps import DB, CurrentUser, client_ip
from app.core.config import settings
from app.core.errors import Conflict, NotFound, Unprocessable
from app.models import Exam
from app.models.enums import CourseRole, JobKind, PaperStatus
from app.providers.storage import get_storage
from app.schemas.common import Message
from app.schemas.exams import ExamCreate, ExamOut, ExamUpdate, QuestionsOut, QuestionsPut
from app.services import access, audit, exam_state, jobs
from app.services.files import store_upload
from app.services.marks import validate_structure
from app.services.structure import apply_structure

router = APIRouter(tags=["exams"])
PAPER_KINDS = {"pdf", "docx", "png", "jpeg"}


@router.get("/exams", response_model=list[ExamOut])
def list_all_exams(db: DB, user: CurrentUser):
    ids = access.course_ids_for(db, user)
    rows = db.scalars(select(Exam).where(Exam.course_id.in_(ids), Exam.deleted_at.is_(None)).order_by(Exam.created_at.desc())).all()
    return [exam_state.exam_out(db, e) for e in rows]


@router.get("/courses/{course_id}/exams", response_model=list[ExamOut])
def list_course_exams(course_id: uuid.UUID, db: DB, user: CurrentUser):
    access.get_course(db, user, course_id)
    rows = db.scalars(select(Exam).where(Exam.course_id == course_id, Exam.deleted_at.is_(None)).order_by(Exam.created_at.desc())).all()
    return [exam_state.exam_out(db, e) for e in rows]


@router.post("/courses/{course_id}/exams", response_model=ExamOut, status_code=201)
def create_exam(course_id: uuid.UUID, body: ExamCreate, request: Request, db: DB, user: CurrentUser):
    course = access.get_course(db, user, course_id, CourseRole.INSTRUCTOR)
    exam = Exam(
        course_id=course.id, created_by=user.id, title=body.title, pass_percentage=body.pass_percentage,
        confidence_high_threshold=body.confidence_high_threshold, confidence_review_threshold=body.confidence_review_threshold,
    )
    db.add(exam)
    db.flush()
    audit.record(db, actor_id=user.id, action="exam.create", entity_type="exam", entity_id=exam.id, course_id=course.id, exam_id=exam.id,
                 after={"title": exam.title}, ip=client_ip(request))
    db.commit()
    return exam_state.exam_out(db, exam)


@router.get("/exams/{exam_id}", response_model=ExamOut)
def get_exam(exam_id: uuid.UUID, db: DB, user: CurrentUser):
    return exam_state.exam_out(db, access.get_exam(db, user, exam_id))


@router.patch("/exams/{exam_id}", response_model=ExamOut)
def update_exam(exam_id: uuid.UUID, body: ExamUpdate, request: Request, db: DB, user: CurrentUser):
    exam = access.get_exam(db, user, exam_id, CourseRole.INSTRUCTOR)
    data = body.model_dump(exclude_unset=True)
    hi = data.get("confidence_high_threshold", exam.confidence_high_threshold)
    lo = data.get("confidence_review_threshold", exam.confidence_review_threshold)
    if hi is not None and lo is not None and lo > hi:
        raise Unprocessable("The mandatory-review threshold cannot exceed the high-confidence threshold.", code="invalid_thresholds")
    before = {k: str(getattr(exam, k)) for k in data}
    for k, v in data.items():
        setattr(exam, k, v)
    audit.record(db, actor_id=user.id, action="exam.update", entity_type="exam", entity_id=exam.id, course_id=exam.course_id, exam_id=exam.id,
                 before=before, after=data, ip=client_ip(request))
    db.commit()
    return exam_state.exam_out(db, exam)


@router.delete("/exams/{exam_id}", response_model=Message)
def delete_exam(exam_id: uuid.UUID, request: Request, db: DB, user: CurrentUser):
    exam = access.get_exam(db, user, exam_id, CourseRole.OWNER)
    exam.deleted_at = datetime.now(timezone.utc)
    audit.record(db, actor_id=user.id, action="exam.delete", entity_type="exam", entity_id=exam.id, course_id=exam.course_id, exam_id=exam.id, ip=client_ip(request))
    db.commit()
    return Message(message="Exam deleted.")


# ------------------------------------------------------------------------------------------ question paper
@router.post("/exams/{exam_id}/question-paper", response_model=ExamOut, status_code=202)
def upload_question_paper(exam_id: uuid.UUID, request: Request, db: DB, user: CurrentUser, file: UploadFile = File(...)):
    exam = access.get_exam(db, user, exam_id, CourseRole.INSTRUCTOR)
    if exam.paper_status == PaperStatus.PROCESSING:
        raise Conflict("The question paper is already being processed.", code="job_already_active")
    if exam_state.rubric_status(db, exam.id) == "APPROVED":
        raise Conflict("A rubric has already been approved for this exam, so the question paper can no longer be replaced.", code="structure_locked")
    storage = get_storage()
    stored = store_upload(file, storage, prefix=f"papers/{exam.course_id}", allowed_kinds=PAPER_KINDS,
                          max_bytes=settings.max_question_paper_upload_mb * 1024 * 1024)
    old = exam.paper_storage_key
    exam.paper_storage_key, exam.paper_filename = stored.storage_key, stored.original_filename
    exam.paper_sha256, exam.paper_size_bytes = stored.sha256, stored.size_bytes
    exam.paper_status, exam.paper_error = PaperStatus.PROCESSING, None
    job = jobs.create_job(db, kind=JobKind.PARSE_QUESTION_PAPER, entity_type="exam", entity_id=exam.id,
                          course_id=exam.course_id, exam_id=exam.id, user_id=user.id)
    audit.record(db, actor_id=user.id, action="exam.upload_paper", entity_type="exam", entity_id=exam.id, course_id=exam.course_id, exam_id=exam.id,
                 after={"filename": stored.original_filename, "sha256": stored.sha256}, ip=client_ip(request))
    db.commit()
    if old and old != stored.storage_key:
        storage.delete(old)
    jobs.dispatch(job.id)
    db.refresh(exam)
    return exam_state.exam_out(db, exam)


@router.post("/exams/{exam_id}/question-paper/reparse", response_model=ExamOut, status_code=202)
def reparse_paper(exam_id: uuid.UUID, db: DB, user: CurrentUser):
    exam = access.get_exam(db, user, exam_id, CourseRole.INSTRUCTOR)
    if not exam.paper_storage_key:
        raise Unprocessable("Upload a question paper first.", code="no_paper")
    jobs.enqueue(db, kind=JobKind.PARSE_QUESTION_PAPER, entity_type="exam", entity_id=exam.id, course_id=exam.course_id, exam_id=exam.id, user_id=user.id)
    db.refresh(exam)
    return exam_state.exam_out(db, exam)


@router.get("/exams/{exam_id}/question-paper/file")
def download_paper(exam_id: uuid.UUID, db: DB, user: CurrentUser):
    exam = access.get_exam(db, user, exam_id)
    if not exam.paper_storage_key or not get_storage().exists(exam.paper_storage_key):
        raise NotFound("No question paper has been uploaded.")
    key = exam.paper_storage_key
    media = "application/pdf" if key.endswith(".pdf") else "image/png" if key.endswith(".png") else "image/jpeg" if key.endswith(".jpg") else "application/octet-stream"
    return StreamingResponse(
        get_storage().open_stream(key), media_type=media,
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(exam.paper_filename or 'question-paper')}", "X-Content-Type-Options": "nosniff"},
    )


# ------------------------------------------------------------------------------------------ questions
@router.get("/exams/{exam_id}/questions", response_model=QuestionsOut)
def get_questions(exam_id: uuid.UUID, db: DB, user: CurrentUser):
    return exam_state.questions_out(db, access.get_exam(db, user, exam_id))


@router.put("/exams/{exam_id}/questions", response_model=QuestionsOut)
def put_questions(exam_id: uuid.UUID, body: QuestionsPut, request: Request, db: DB, user: CurrentUser):
    exam = access.get_exam(db, user, exam_id, CourseRole.INSTRUCTOR)
    if exam.paper_status == PaperStatus.PROCESSING:
        raise Conflict("Wait for the paper to finish processing before editing questions.", code="job_already_active")
    changed = apply_structure(db, exam, body.questions)
    if exam.paper_status == PaperStatus.NONE:
        exam.paper_status = PaperStatus.PARSED  # questions entered manually
    audit.record(db, actor_id=user.id, action="exam.edit_questions", entity_type="exam", entity_id=exam.id, course_id=exam.course_id, exam_id=exam.id,
                 after={"question_count": len(body.questions), "structure_changed": changed}, ip=client_ip(request))
    db.commit()
    return exam_state.questions_out(db, exam)


@router.post("/exams/{exam_id}/questions/confirm", response_model=QuestionsOut)
def confirm_questions(exam_id: uuid.UUID, request: Request, db: DB, user: CurrentUser):
    """The teacher signs off the parsed structure. Blocked while validation errors remain."""
    exam = access.get_exam(db, user, exam_id, CourseRole.INSTRUCTOR)
    report = validate_structure(exam_state.load_questions(db, exam.id), exam.declared_total_marks)
    if report.has_errors:
        raise Unprocessable("Fix the highlighted problems before confirming the questions.", code="questions_invalid",
                            details=[i.as_dict() for i in report.issues if i.level == "error"])
    exam.questions_confirmed_at = datetime.now(timezone.utc)
    audit.record(db, actor_id=user.id, action="exam.confirm_questions", entity_type="exam", entity_id=exam.id, course_id=exam.course_id, exam_id=exam.id,
                 after={"computed_total": float(report.computed_total), "declared_total": float(report.declared_total) if report.declared_total else None},
                 ip=client_ip(request))
    db.commit()
    return exam_state.questions_out(db, exam)
