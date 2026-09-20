"""API views of answer sheets (list summary and full detail with the OCR/answer/mapping trace)."""
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Answer,
    AnswerMapping,
    AnswerPage,
    AnswerSheet,
    ConfidenceFlag,
    Evaluation,
    OcrResult,
    ProcessingJob,
    Question,
    Student,
)
from app.models.enums import JobKind
from app.schemas.jobs import JobOut
from app.schemas.sheets import AnswerOut, FlagOut, MappingOut, PageOut, SheetDetailOut, SheetOut, StudentOut
from app.services.marks import units_of


def _job(db: Session, sheet_id: uuid.UUID) -> ProcessingJob | None:
    return db.scalar(
        select(ProcessingJob)
        .where(ProcessingJob.kind.in_([JobKind.PROCESS_ANSWER_SHEET, JobKind.EVALUATE_ANSWER_SHEET]), ProcessingJob.entity_id == sheet_id)
        .order_by(ProcessingJob.queued_at.desc())
        .limit(1)
    )


def sheet_out(db: Session, sheet: AnswerSheet) -> SheetOut:
    student = db.get(Student, sheet.student_id) if sheet.student_id else None
    job = _job(db, sheet.id)
    out = SheetOut.model_validate(sheet)
    out.student = StudentOut.model_validate(student) if student else None
    out.job = JobOut.model_validate(job) if job else None
    out.open_flags = db.scalar(select(func.count()).select_from(ConfidenceFlag).where(ConfidenceFlag.answer_sheet_id == sheet.id, ConfidenceFlag.resolved_at.is_(None))) or 0
    out.answer_count = db.scalar(select(func.count()).select_from(Answer).where(Answer.answer_sheet_id == sheet.id)) or 0
    out.missing_count = db.scalar(select(func.count()).select_from(Answer).where(Answer.answer_sheet_id == sheet.id, Answer.is_missing)) or 0
    out.evaluated_count = db.scalar(
        select(func.count()).select_from(Evaluation).join(Answer, Answer.id == Evaluation.answer_id).where(Answer.answer_sheet_id == sheet.id, Evaluation.is_current)
    ) or 0
    return out


def sheet_detail(db: Session, sheet: AnswerSheet) -> SheetDetailOut:
    base = sheet_out(db, sheet)
    questions = list(db.scalars(select(Question).where(Question.exam_id == sheet.exam_id).order_by(Question.position)))
    units = {(u.question_id, u.subquestion_id): u for u in units_of(questions)}
    qmap = {q.id: q for q in questions}

    pages = list(db.scalars(select(AnswerPage).where(AnswerPage.answer_sheet_id == sheet.id).order_by(AnswerPage.page_number)))
    ocr_by_page = {o.answer_page_id: o for o in db.scalars(select(OcrResult).where(OcrResult.answer_page_id.in_([p.id for p in pages]), OcrResult.is_current))}
    page_no = {p.id: p.page_number for p in pages}
    page_out = [
        PageOut(
            page_number=p.page_number, width=p.width, height=p.height, image_url=f"/api/answer-sheets/{sheet.id}/pages/{p.page_number}/image",
            ocr_confidence=(ocr_by_page[p.id].confidence if p.id in ocr_by_page else None),
            has_diagram=bool(ocr_by_page[p.id].has_diagram) if p.id in ocr_by_page else False,
            unreadable_spans=[str(x) for x in (ocr_by_page[p.id].unreadable_spans or [])] if p.id in ocr_by_page else [],
            ocr_text=ocr_by_page[p.id].text if p.id in ocr_by_page else "",
        )
        for p in pages
    ]

    all_maps = list(db.scalars(select(AnswerMapping).where(AnswerMapping.answer_sheet_id == sheet.id).order_by(AnswerMapping.segment_order)))

    def m_out(m: AnswerMapping) -> MappingOut:
        return MappingOut(id=m.id, answer_id=m.answer_id, page_number=page_no.get(m.answer_page_id, 0), detected_label=m.detected_label,
                          text=m.text, confidence=m.confidence, method=m.method, is_active=m.is_active)

    answers_out: list[AnswerOut] = []
    answer_rows = {(a.question_id, a.subquestion_id): a for a in db.scalars(select(Answer).where(Answer.answer_sheet_id == sheet.id))}
    for key, u in units.items():
        a = answer_rows.get(key)
        if a is None:
            continue
        q = qmap[u.question_id]
        text = next((s.text for s in q.subquestions if s.id == u.subquestion_id), q.text) if u.subquestion_id else q.text
        maps = [m for m in all_maps if m.answer_id == a.id]
        answers_out.append(AnswerOut(
            id=a.id, question_id=a.question_id, subquestion_id=a.subquestion_id, label=u.label, question_text=text, max_marks=u.max_marks,
            original_text=a.original_text, corrected_text=a.corrected_text, effective_text=a.effective_text, is_missing=a.is_missing,
            has_diagram=a.has_diagram, ocr_confidence=a.ocr_confidence, mapping_confidence=a.mapping_confidence, corrected_at=a.corrected_at,
            pages=sorted({page_no.get(m.answer_page_id, 0) for m in maps if m.is_active}), mappings=[m_out(m) for m in maps],
        ))
    flags = db.scalars(select(ConfidenceFlag).where(ConfidenceFlag.answer_sheet_id == sheet.id).order_by(ConfidenceFlag.created_at))
    return SheetDetailOut(
        **base.model_dump(),
        pages=page_out,
        answers=answers_out,
        unassigned=[m_out(m) for m in all_maps if m.answer_id is None and m.is_active],
        flags=[FlagOut.model_validate(f) for f in flags],
    )
