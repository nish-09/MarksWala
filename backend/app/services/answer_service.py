"""Teacher-driven edits of what was read from an answer sheet: OCR correction, segment reassignment, student assignment.

Original OCR is never modified: corrections are stored beside it and every change is audited.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, Unprocessable
from app.models import Answer, AnswerMapping, AnswerSheet, ConfidenceFlag, Exam, OcrResult, Student, User
from app.models.enums import FlagKind, MappingMethod
from app.services import audit
from app.services.sheet_service import norm_roll


def rebuild_answer(db: Session, answer: Answer) -> None:
    """Recompute an answer's OCR-derived fields from its active mappings (used after a reassignment)."""
    maps = list(db.scalars(select(AnswerMapping).where(AnswerMapping.answer_id == answer.id, AnswerMapping.is_active).order_by(AnswerMapping.segment_order)))
    answer.original_text = "\n".join(m.text for m in maps)
    answer.is_missing = not answer.original_text.strip()
    answer.has_diagram = "[DIAGRAM" in answer.original_text.upper()
    if maps:
        conf = {r.id: r.confidence for r in db.scalars(select(OcrResult).where(OcrResult.id.in_({m.ocr_result_id for m in maps})))}
        weights = [max(len(m.text), 1) for m in maps]
        answer.ocr_confidence = round(sum(conf[m.ocr_result_id] * w for m, w in zip(maps, weights)) / sum(weights), 3)
        answer.mapping_confidence = round(min(m.confidence for m in maps), 3)
    else:
        answer.ocr_confidence = answer.mapping_confidence = None


def set_corrected_text(db: Session, user: User, answer: Answer, sheet: AnswerSheet, exam: Exam, text: str | None, ip: str | None) -> Answer:
    before = answer.corrected_text
    if text is not None and text.strip() == answer.original_text.strip():
        text = None  # identical to the original: not a correction
    answer.corrected_text = text
    answer.corrected_by = user.id if text is not None else None
    answer.corrected_at = datetime.now(timezone.utc) if text is not None else None
    audit.record(db, actor_id=user.id, action="answer.correct_ocr" if text is not None else "answer.revert_ocr", entity_type="answer", entity_id=answer.id,
                 course_id=exam.course_id, exam_id=exam.id, before={"corrected_text": before}, after={"corrected_text": text, "original_text": answer.original_text}, ip=ip)
    return answer


def reassign_mapping(db: Session, user: User, mapping: AnswerMapping, sheet: AnswerSheet, exam: Exam, target_answer_id: uuid.UUID | None, ip: str | None) -> AnswerMapping:
    if not mapping.is_active:
        raise Conflict("This segment was already reassigned.", code="mapping_inactive")
    target = None
    if target_answer_id is not None:
        target = db.get(Answer, target_answer_id)
        if target is None or target.answer_sheet_id != sheet.id:
            raise NotFound("The target answer does not belong to this answer sheet.")
    if target_answer_id == mapping.answer_id:
        return mapping
    old_answer = db.get(Answer, mapping.answer_id) if mapping.answer_id else None
    mapping.is_active = False
    new = AnswerMapping(
        answer_sheet_id=sheet.id, answer_id=target_answer_id, answer_page_id=mapping.answer_page_id, ocr_result_id=mapping.ocr_result_id,
        segment_order=mapping.segment_order, detected_label=mapping.detected_label, text=mapping.text, confidence=1.0,
        method=MappingMethod.TEACHER, is_active=True,
    )
    db.add(new)
    db.flush()
    for a in (old_answer, target):
        if a is not None:
            rebuild_answer(db, a)
    audit.record(db, actor_id=user.id, action="mapping.reassign", entity_type="answer_mapping", entity_id=new.id, course_id=exam.course_id, exam_id=exam.id,
                 before={"answer_id": str(mapping.answer_id) if mapping.answer_id else None}, after={"answer_id": str(target_answer_id) if target_answer_id else None}, ip=ip)
    still_unassigned = db.scalar(select(func.count()).select_from(AnswerMapping).where(
        AnswerMapping.answer_sheet_id == sheet.id, AnswerMapping.answer_id.is_(None), AnswerMapping.is_active))
    if not still_unassigned:
        db.execute(update(ConfidenceFlag).where(
            ConfidenceFlag.answer_sheet_id == sheet.id, ConfidenceFlag.kind == FlagKind.LOW_MAPPING, ConfidenceFlag.answer_id.is_(None),
            ConfidenceFlag.evaluation_id.is_(None), ConfidenceFlag.resolved_at.is_(None)).values(resolved_at=datetime.now(timezone.utc)))
    return new


def assign_student(db: Session, user: User, sheet: AnswerSheet, exam: Exam, roll_number: str, full_name: str | None, ip: str | None) -> Student:
    roll = norm_roll(roll_number)
    if not roll:
        raise Unprocessable("A roll number is required.", code="roll_required")
    student = db.scalar(select(Student).where(Student.course_id == exam.course_id, func.upper(Student.roll_number) == roll, Student.deleted_at.is_(None)))
    if student is None:
        name = (full_name or sheet.detected_name or "").strip()
        if not name:
            raise Unprocessable("This roll number is new: enter the student's name too.", code="name_required")
        student = Student(course_id=exam.course_id, roll_number=roll, full_name=name[:200])
        db.add(student)
        db.flush()
    elif full_name and full_name.strip() and full_name.strip() != student.full_name:
        student.full_name = full_name.strip()[:200]
    clash = db.scalar(select(AnswerSheet.id).where(AnswerSheet.exam_id == exam.id, AnswerSheet.student_id == student.id, AnswerSheet.deleted_at.is_(None), AnswerSheet.id != sheet.id))
    if clash:
        raise Conflict(f"{student.full_name} ({student.roll_number}) already has an answer sheet in this exam.", code="student_has_sheet")
    before = str(sheet.student_id) if sheet.student_id else None
    sheet.student_id = student.id
    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        raise Conflict("That student already has an answer sheet in this exam.", code="student_has_sheet") from e
    db.execute(update(ConfidenceFlag).where(
        ConfidenceFlag.answer_sheet_id == sheet.id, ConfidenceFlag.kind == FlagKind.STUDENT_UNIDENTIFIED, ConfidenceFlag.resolved_at.is_(None)
    ).values(resolved_at=datetime.now(timezone.utc)))
    audit.record(db, actor_id=user.id, action="sheet.assign_student", entity_type="answer_sheet", entity_id=sheet.id, course_id=exam.course_id, exam_id=exam.id,
                 before={"student_id": before}, after={"student_id": str(student.id), "roll_number": student.roll_number}, ip=ip)
    return student
