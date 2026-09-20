from __future__ import annotations

import uuid
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from app.api.deps import DB, CurrentUser, client_ip
from app.core.errors import Conflict, NotFound, Unprocessable
from app.models import AnswerMapping, AnswerPage, AnswerSheet, Student
from app.models.enums import CourseRole, JobKind, SheetStatus
from app.providers.storage import get_storage
from app.schemas.common import Message
from app.schemas.sheets import (
    AnswerOut,
    CorrectedTextIn,
    MappingOut,
    ReassignIn,
    SheetDetailOut,
    SheetOut,
    StudentAssignIn,
    StudentSummary,
    UploadItem,
    UploadResponse,
)
from app.services import access, answer_service, audit, jobs, sheet_views

router = APIRouter(tags=["answer sheets"])


@router.post("/exams/{exam_id}/answer-sheets", response_model=UploadResponse, status_code=201)
def upload_sheets(exam_id: uuid.UUID, request: Request, db: DB, user: CurrentUser, files: list[UploadFile] = File(...)):
    from app.services import sheet_service

    exam = access.get_exam(db, user, exam_id, CourseRole.INSTRUCTOR)
    if exam.questions_confirmed_at is None:
        raise Unprocessable("Confirm the parsed questions before uploading answer sheets.", code="questions_not_confirmed")
    if not files:
        raise Unprocessable("Choose at least one PDF.", code="no_files")
    if len(files) > 100:
        raise Unprocessable("Upload at most 100 answer sheets at a time.", code="too_many_files")
    batch, outcomes = sheet_service.create_sheets(db, user, exam, files, client_ip(request))
    accepted = [o for o in outcomes if o.sheet is not None]
    if not accepted:
        db.rollback()
        first = outcomes[0]
        raise Unprocessable(first.error_message or "No files were accepted.", code=first.error_code or "rejected",
                            details=[{"filename": o.filename, "code": o.error_code, "message": o.error_message} for o in outcomes])
    job_ids: list[uuid.UUID] = []
    for o in accepted:
        job = jobs.create_job(db, kind=JobKind.PROCESS_ANSWER_SHEET, entity_type="answer_sheet", entity_id=o.sheet.id,
                              course_id=exam.course_id, exam_id=exam.id, user_id=user.id)
        job_ids.append(job.id)
    db.commit()
    for jid in job_ids:
        try:
            jobs.dispatch(jid)
        except Exception:  # dispatch() already marked that job FAILED with a clear reason; keep the rest going
            pass
    return UploadResponse(
        batch_id=batch.id,
        results=[
            UploadItem(filename=o.filename, ok=o.sheet is not None, error_code=o.error_code, error_message=o.error_message,
                       sheet=sheet_views.sheet_out(db, o.sheet) if o.sheet else None)
            for o in outcomes
        ],
    )


@router.get("/exams/{exam_id}/answer-sheets", response_model=list[SheetOut])
def list_sheets(exam_id: uuid.UUID, db: DB, user: CurrentUser):
    access.get_exam(db, user, exam_id)
    rows = db.scalars(select(AnswerSheet).where(AnswerSheet.exam_id == exam_id, AnswerSheet.deleted_at.is_(None)).order_by(AnswerSheet.created_at)).all()
    return [sheet_views.sheet_out(db, s) for s in rows]


@router.get("/answer-sheets/{sheet_id}", response_model=SheetDetailOut)
def get_sheet(sheet_id: uuid.UUID, db: DB, user: CurrentUser):
    sheet, _ = access.get_sheet(db, user, sheet_id)
    return sheet_views.sheet_detail(db, sheet)


@router.get("/answer-sheets/{sheet_id}/pages/{page_number}/image")
def page_image(sheet_id: uuid.UUID, page_number: int, db: DB, user: CurrentUser):
    sheet, _ = access.get_sheet(db, user, sheet_id)
    page = db.scalar(select(AnswerPage).where(AnswerPage.answer_sheet_id == sheet.id, AnswerPage.page_number == page_number))
    if page is None or not get_storage().exists(page.image_key):
        raise NotFound("Page image not found.")
    return StreamingResponse(get_storage().open_stream(page.image_key), media_type="image/png",
                             headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})


@router.get("/answer-sheets/{sheet_id}/file")
def sheet_file(sheet_id: uuid.UUID, db: DB, user: CurrentUser):
    sheet, _ = access.get_sheet(db, user, sheet_id)
    if not get_storage().exists(sheet.storage_key):
        raise NotFound("The stored file is missing.")
    return StreamingResponse(get_storage().open_stream(sheet.storage_key), media_type="application/pdf",
                             headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(sheet.original_filename)}", "X-Content-Type-Options": "nosniff"})


@router.delete("/answer-sheets/{sheet_id}", response_model=Message)
def delete_sheet(sheet_id: uuid.UUID, request: Request, db: DB, user: CurrentUser):
    sheet, exam = access.get_sheet(db, user, sheet_id, CourseRole.INSTRUCTOR)
    if sheet.status in (SheetStatus.PROCESSING, SheetStatus.EVALUATING):
        raise Conflict("Wait for processing to finish before deleting this answer sheet.", code="job_already_active")
    sheet.deleted_at = datetime.now(timezone.utc)
    audit.record(db, actor_id=user.id, action="sheet.delete", entity_type="answer_sheet", entity_id=sheet.id, course_id=exam.course_id, exam_id=exam.id, ip=client_ip(request))
    db.commit()
    return Message(message="Answer sheet deleted.")


@router.post("/answer-sheets/{sheet_id}/reprocess", response_model=SheetOut, status_code=202)
def reprocess(sheet_id: uuid.UUID, db: DB, user: CurrentUser):
    sheet, exam = access.get_sheet(db, user, sheet_id, CourseRole.INSTRUCTOR)
    if sheet.status in (SheetStatus.EVALUATED, SheetStatus.EVALUATING):
        raise Conflict("This sheet has evaluations; reprocessing would discard graded work and teacher decisions.", code="already_evaluated")
    jobs.enqueue(db, kind=JobKind.PROCESS_ANSWER_SHEET, entity_type="answer_sheet", entity_id=sheet.id, course_id=exam.course_id, exam_id=exam.id, user_id=user.id)
    db.refresh(sheet)
    return sheet_views.sheet_out(db, sheet)


@router.put("/answer-sheets/{sheet_id}/student", response_model=SheetOut)
def set_student(sheet_id: uuid.UUID, body: StudentAssignIn, request: Request, db: DB, user: CurrentUser):
    sheet, exam = access.get_sheet(db, user, sheet_id, CourseRole.INSTRUCTOR)
    answer_service.assign_student(db, user, sheet, exam, body.roll_number, body.full_name, client_ip(request))
    db.commit()
    from app.services import results_service

    results_service.recompute_sheet(db, sheet.id)
    db.commit()
    return sheet_views.sheet_out(db, sheet)


@router.post("/answer-mappings/{mapping_id}/reassign", response_model=MappingOut)
def reassign(mapping_id: uuid.UUID, body: ReassignIn, request: Request, db: DB, user: CurrentUser):
    m = db.get(AnswerMapping, mapping_id)
    if m is None:
        raise NotFound("Segment not found.")
    sheet, exam = access.get_sheet(db, user, m.answer_sheet_id, CourseRole.INSTRUCTOR)
    new = answer_service.reassign_mapping(db, user, m, sheet, exam, body.answer_id, client_ip(request))
    db.commit()
    page = db.get(AnswerPage, new.answer_page_id)
    return MappingOut(id=new.id, answer_id=new.answer_id, page_number=page.page_number, detected_label=new.detected_label, text=new.text,
                      confidence=new.confidence, method=new.method, is_active=new.is_active)


@router.put("/answers/{answer_id}/corrected-text", response_model=AnswerOut)
def correct_text(answer_id: uuid.UUID, body: CorrectedTextIn, request: Request, db: DB, user: CurrentUser):
    answer, sheet, exam = access.get_answer(db, user, answer_id, CourseRole.INSTRUCTOR)
    answer_service.set_corrected_text(db, user, answer, sheet, exam, body.text, client_ip(request))
    db.commit()
    detail = sheet_views.sheet_detail(db, sheet)
    return next(a for a in detail.answers if a.id == answer.id)


# ------------------------------------------------------------------------------------------------ students
@router.get("/courses/{course_id}/students", response_model=list[StudentSummary])
def list_students(course_id: uuid.UUID, db: DB, user: CurrentUser):
    access.get_course(db, user, course_id)
    rows = db.execute(
        select(Student, func.count(AnswerSheet.id))
        .outerjoin(AnswerSheet, (AnswerSheet.student_id == Student.id) & (AnswerSheet.deleted_at.is_(None)))
        .where(Student.course_id == course_id, Student.deleted_at.is_(None))
        .group_by(Student.id)
        .order_by(Student.roll_number)
    ).all()
    return [StudentSummary(id=s.id, roll_number=s.roll_number, full_name=s.full_name, sheet_count=n) for s, n in rows]
