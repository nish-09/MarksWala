"""Question-structure persistence shared by the AI parser and the teacher's editor.

`apply_structure` preserves existing row ids (so rubrics/answers keep pointing at the same questions),
validates the input, and refuses structural or marks changes once they could invalidate graded work.
"""
from __future__ import annotations

import re
import uuid
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, Unprocessable
from app.models import AnswerSheet, Exam, Question, Rubric, RubricCriterion, RubricVersion, Subquestion
from app.models.enums import RubricVersionStatus
from app.schemas.exams import QuestionIn
from app.services.marks import q2


def structure_locked(db: Session, exam: Exam) -> bool:
    """True once marks/structure changes could invalidate approved rubrics or graded answers."""
    has_frozen_rubric = db.scalar(
        select(func.count())
        .select_from(RubricVersion)
        .join(Rubric, Rubric.id == RubricVersion.rubric_id)
        .where(Rubric.exam_id == exam.id, RubricVersion.status != RubricVersionStatus.DRAFT)
    )
    has_sheets = db.scalar(select(func.count()).select_from(AnswerSheet).where(AnswerSheet.exam_id == exam.id, AnswerSheet.deleted_at.is_(None)))
    return bool(has_frozen_rubric or has_sheets)


def norm_label(label: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", label).lower()


def _validate(items: list[QuestionIn]) -> None:
    nums = [q.number for q in items]
    if len(set(nums)) != len(nums):
        dup = sorted({n for n in nums if nums.count(n) > 1})
        raise Unprocessable(f"Question numbers must be unique (duplicated: {', '.join(map(str, dup))}).", code="duplicate_question_number")
    for q in items:
        labels = [norm_label(s.label) for s in q.subquestions]
        if any(not l for l in labels):
            raise Unprocessable(f"Q{q.number} has a sub-part with an empty label.", code="invalid_label")
        if len(set(labels)) != len(labels):
            raise Unprocessable(f"Q{q.number} has duplicate sub-part labels.", code="duplicate_label")
        if not q.subquestions and not q.text.strip():
            raise Unprocessable(f"Q{q.number} has no text.", code="empty_question")
    groups: dict[str, int] = {}
    for q in items:
        if q.choice_group:
            groups[q.choice_group] = groups.get(q.choice_group, 0) + 1


def _resolve_missing_ids(items: list[QuestionIn], existing: dict[uuid.UUID, Question]) -> None:
    """Clients may omit ids: match questions by number and sub-parts by label so an edit is idempotent."""
    claimed = {q.id for q in items if q.id}
    by_number = {q.number: q for q in existing.values()}
    for qi in items:
        if qi.id is None and (cur := by_number.get(qi.number)) is not None and cur.id not in claimed:
            qi.id = cur.id
            claimed.add(cur.id)
        cur = existing.get(qi.id) if qi.id else None
        if cur is None:
            continue
        sub_claimed = {s.id for s in qi.subquestions if s.id}
        by_label = {s.label: s for s in cur.subquestions}
        for si in qi.subquestions:
            if si.id is None and (cs := by_label.get(norm_label(si.label))) is not None and cs.id not in sub_claimed:
                si.id = cs.id
                sub_claimed.add(cs.id)


def apply_structure(db: Session, exam: Exam, items: list[QuestionIn]) -> bool:
    """Create/update/delete questions so they match `items`. Returns True if structure or marks changed."""
    _validate(items)
    existing_q = {q.id: q for q in db.scalars(select(Question).where(Question.exam_id == exam.id))}
    _resolve_missing_ids(items, existing_q)
    locked = structure_locked(db, exam)

    # -- compare against the current state to decide whether the change is structural ------------------
    incoming_ids = {q.id for q in items if q.id}
    changed = False
    if set(existing_q) != incoming_ids or any(q.id is None for q in items):
        changed = True
    else:
        for qi in items:
            cur = existing_q[qi.id]
            cur_subs = {s.id: s for s in cur.subquestions}
            sub_ids = {s.id for s in qi.subquestions if s.id}
            if cur.number != qi.number or set(cur_subs) != sub_ids or any(s.id is None for s in qi.subquestions):
                changed = True
            elif qi.subquestions:
                if any(q2(s.max_marks) != cur_subs[s.id].max_marks for s in qi.subquestions):
                    changed = True
            elif q2(qi.max_marks) != cur.max_marks:
                changed = True
            if (qi.choice_group or None) != cur.choice_group or qi.choice_count != cur.choice_count:
                changed = True
    if changed and locked:
        raise Conflict(
            "This exam already has an approved rubric or uploaded answer sheets, so questions, sub-parts, marks and "
            "internal-choice settings can no longer change. You can still fix question text and topics. "
            "To change the structure, create a new exam.",
            code="structure_locked",
        )

    # -- delete removed questions (and draft rubric criteria that point at them) ---------------------------
    removed = [q for qid, q in existing_q.items() if qid not in incoming_ids]
    for q in removed:
        db.execute(delete(RubricCriterion).where(RubricCriterion.question_id == q.id))
        db.delete(q)
    db.flush()

    # -- free unique (number / label) slots so numbers can be swapped safely -------------------------------
    for i, q in enumerate(q for q in existing_q.values() if q.id in incoming_ids):
        q.number = -(i + 1)
        for j, s in enumerate(q.subquestions):
            s.label = f"~{j}"
    db.flush()

    # -- upsert ------------------------------------------------------------------------------------------
    for pos, qi in enumerate(sorted(items, key=lambda x: x.number)):
        q = existing_q.get(qi.id) if qi.id else None
        if q is None or q.id not in incoming_ids:
            q = Question(exam_id=exam.id, number=qi.number, position=pos, text="", max_marks=Decimal(0))
            db.add(q)
            db.flush()
        q.number, q.position = qi.number, pos
        q.section = (qi.section or None) and qi.section.strip()
        q.text = qi.text.strip()
        q.topic = (qi.topic or None) and qi.topic.strip()
        q.choice_group = (qi.choice_group or None) and qi.choice_group.strip()
        q.choice_count = qi.choice_count if q.choice_group else 1

        current_subs = {s.id: s for s in q.subquestions}
        keep = {s.id for s in qi.subquestions if s.id}
        for sid, s in list(current_subs.items()):
            if sid not in keep:
                db.execute(delete(RubricCriterion).where(RubricCriterion.subquestion_id == sid))
                db.delete(s)
        db.flush()
        total = Decimal(0)
        for spos, si in enumerate(qi.subquestions):
            s = current_subs.get(si.id) if si.id else None
            if s is None or s.id not in keep:
                s = Subquestion(question_id=q.id, label="~new", position=spos, text="", max_marks=Decimal(0))
                db.add(s)
            s.label, s.position = norm_label(si.label), spos
            s.text = si.text.strip()
            s.max_marks = q2(si.max_marks)
            s.topic = (si.topic or None) and si.topic.strip()
            total += s.max_marks
        q.max_marks = q2(total) if qi.subquestions else q2(qi.max_marks)
    db.flush()
    db.expire_all()
    if changed:
        exam.questions_confirmed_at = None
    return changed
