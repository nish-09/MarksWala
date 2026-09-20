"""Exam analytics. Every number is computed from persisted evaluations, teacher overrides and results.

Question statistics are over students who ATTEMPTED the question (non-empty answer), and an unattempted
alternative of an internal-choice question is never counted against anyone. `attempts` shows how many did.
"""
from __future__ import annotations

import statistics
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Answer,
    AnswerSheet,
    Evaluation,
    EvaluationCriterion,
    Exam,
    ExamAnalytics,
    Question,
    RubricCriterion,
    Student,
    TeacherOverride,
)
from app.services import exam_state, results_service
from app.services.marks import units_of

WEAK_BELOW = 50.0  # % at which a topic/criterion counts as a weakness
STRONG_FROM = 80.0


def _f(v: Decimal | float | None) -> float:
    return float(v or 0)


def _pct(score: float, mx: float) -> float:
    return round(score / mx * 100, 2) if mx > 0 else 0.0


def compute(db: Session, exam: Exam) -> dict:
    questions = list(db.scalars(select(Question).where(Question.exam_id == exam.id).order_by(Question.position)))
    units = units_of(questions)
    sheets = list(db.scalars(select(AnswerSheet).where(AnswerSheet.exam_id == exam.id, AnswerSheet.deleted_at.is_(None), AnswerSheet.status == "EVALUATED")))
    students = {s.id: s for s in db.scalars(select(Student).where(Student.id.in_([x.student_id for x in sheets if x.student_id])))} if sheets else {}
    results = {sh.id: results_service.compute_sheet_result(db, sh.id) for sh in sheets}

    # ---- gather per-answer, per-criterion effective scores (one batch) ---------------------------------------------
    answers = list(db.scalars(select(Answer).where(Answer.answer_sheet_id.in_([s.id for s in sheets])))) if sheets else []
    evs = {e.answer_id: e for e in db.scalars(select(Evaluation).where(Evaluation.answer_id.in_([a.id for a in answers]), Evaluation.is_current))} if answers else {}
    crits = list(db.scalars(select(EvaluationCriterion).where(EvaluationCriterion.evaluation_id.in_([e.id for e in evs.values()])))) if evs else []
    ovr = {o.evaluation_criterion_id: o for o in db.scalars(select(TeacherOverride).where(TeacherOverride.evaluation_id.in_([e.id for e in evs.values()]), TeacherOverride.is_active))} if evs else {}
    rcrit = {c.id: c for c in db.scalars(select(RubricCriterion).where(RubricCriterion.id.in_([c.rubric_criterion_id for c in crits])))} if crits else {}
    crit_by_eval: dict[uuid.UUID, list[EvaluationCriterion]] = {}
    for c in crits:
        crit_by_eval.setdefault(c.evaluation_id, []).append(c)
    ans_by_key = {(a.answer_sheet_id, a.question_id, a.subquestion_id): a for a in answers}

    # ---- class ----------------------------------------------------------------------------------------------------------
    pcts = [_f(r.percentage) for r in results.values()]
    totals = [_f(r.total) for r in results.values()]
    dist_edges = list(range(0, 100, 10))
    dist = [{"range": f"{lo}-{lo + 10 if lo < 90 else 100}%", "from": lo, "to": lo + 10, "count": 0} for lo in dist_edges]
    for p in pcts:
        dist[min(int(p // 10), 9)]["count"] += 1
    pass_pct = exam_state.pass_percentage(exam)
    class_stats = {
        "students": len(results),
        "final": sum(1 for r in results.values() if r.status.value == "FINAL"),
        "provisional": sum(1 for r in results.values() if r.status.value != "FINAL"),
        "max_total": _f(next(iter(results.values())).max_total) if results else _f(exam_state.exam_total_for(db, exam)),
        "average": round(statistics.fmean(totals), 2) if totals else None,
        "average_percentage": round(statistics.fmean(pcts), 2) if pcts else None,
        "median": round(statistics.median(totals), 2) if totals else None,
        "highest": max(totals) if totals else None,
        "lowest": min(totals) if totals else None,
        "pass_percentage_threshold": float(pass_pct),
        "pass_rate": round(sum(1 for p in pcts if p >= float(pass_pct)) / len(pcts) * 100, 2) if pcts else None,
        "distribution": dist,
    }

    # ---- questions ----------------------------------------------------------------------------------------------------------
    q_stats = []
    for u in units:
        scores: list[float] = []
        crit_acc: dict[uuid.UUID, dict] = {}
        for sh in sheets:
            a = ans_by_key.get((sh.id, u.question_id, u.subquestion_id))
            if a is None or a.is_missing or not a.effective_text.strip():
                continue
            e = evs.get(a.id)
            if e is None:
                continue
            total = 0.0
            for c in crit_by_eval.get(e.id, []):
                fin = _f(ovr[c.id].final_score if c.id in ovr else c.score)
                total += fin
                acc = crit_acc.setdefault(c.rubric_criterion_id, {"got": 0.0, "max": 0.0, "n": 0, "zero": 0})
                acc["got"] += fin
                acc["max"] += _f(c.max_score)
                acc["n"] += 1
                acc["zero"] += 1 if fin == 0 else 0
            scores.append(total)
        missed = sorted(
            [{"criterion": rcrit[cid].title, "percentage": _pct(v["got"], v["max"]), "students_scoring_zero": v["zero"], "attempts": v["n"]}
             for cid, v in crit_acc.items() if cid in rcrit],
            key=lambda x: (x["percentage"], -x["students_scoring_zero"]),
        )
        mx = _f(u.max_marks)
        avg = round(statistics.fmean(scores), 2) if scores else None
        q_stats.append({
            "label": u.label, "topic": u.topic, "max_marks": mx, "attempts": len(scores), "students": len(sheets),
            "average": avg, "percentage": _pct(avg, mx) if avg is not None else None,
            "highest": max(scores) if scores else None, "lowest": min(scores) if scores else None,
            "most_missed_criteria": missed[:3],
        })

    # ---- topics --------------------------------------------------------------------------------------------------------------
    topic_acc: dict[str, dict] = {}
    for r in results.values():
        for t, (sc, mx) in r.topics.items():
            a = topic_acc.setdefault(t, {"score": 0.0, "max": 0.0, "students": 0, "per_student": []})
            a["score"] += _f(sc)
            a["max"] += _f(mx)
            a["students"] += 1
            a["per_student"].append(_pct(_f(sc), _f(mx)))
    topics = sorted(
        [{"topic": t, "average_percentage": _pct(v["score"], v["max"]), "students": v["students"],
          "class": "weak" if _pct(v["score"], v["max"]) < WEAK_BELOW else "strong" if _pct(v["score"], v["max"]) >= STRONG_FROM else "moderate"}
         for t, v in topic_acc.items()], key=lambda x: x["average_percentage"])

    # ---- students ------------------------------------------------------------------------------------------------------------
    student_rows = []
    for sh in sheets:
        r = results[sh.id]
        st = students.get(sh.student_id) if sh.student_id else None
        qs = []
        rubric_failures = []
        for us in r.units:
            e = evs.get(us.answer_id) if us.answer_id else None
            qs.append({"label": us.unit.label, "score": _f(us.final_score), "max": _f(us.unit.max_marks), "counted": us.counted,
                       "overridden": us.overridden, "evaluated": us.evaluated})
            if e and us.counted:
                for c in crit_by_eval.get(e.id, []):
                    fin = _f(ovr[c.id].final_score if c.id in ovr else c.score)
                    if fin < _f(c.max_score) and c.rubric_criterion_id in rcrit:
                        rubric_failures.append({"question": us.unit.label, "criterion": rcrit[c.rubric_criterion_id].title, "scored": fin, "max": _f(c.max_score),
                                                "missing": c.missing_points})
        tps = [{"topic": t, "percentage": _pct(_f(sc), _f(mx)), "score": _f(sc), "max": _f(mx)} for t, (sc, mx) in r.topics.items()]
        student_rows.append({
            "sheet_id": str(sh.id), "student_id": str(st.id) if st else None, "roll_number": st.roll_number if st else None,
            "name": st.full_name if st else sh.original_filename, "total": _f(r.total), "max_total": _f(r.max_total), "percentage": _f(r.percentage),
            "passed": r.passed, "status": r.status.value, "questions": qs, "topics": sorted(tps, key=lambda x: x["percentage"]),
            "weak_topics": [t["topic"] for t in tps if t["percentage"] < WEAK_BELOW], "strong_topics": [t["topic"] for t in tps if t["percentage"] >= STRONG_FROM],
            "rubric_failures": rubric_failures,
        })
    student_rows.sort(key=lambda s: (-s["percentage"], s["name"]))
    return {"exam_id": str(exam.id), "exam_title": exam.title, "computed_at": datetime.now(timezone.utc).isoformat(),
            "class": class_stats, "questions": q_stats, "topics": topics, "students": student_rows}


def compute_and_store(db: Session, exam: Exam) -> dict:
    data = compute(db, exam)
    row = db.scalar(select(ExamAnalytics).where(ExamAnalytics.exam_id == exam.id))
    if row is None:
        db.add(ExamAnalytics(exam_id=exam.id, data=data))
    else:
        row.data, row.computed_at = data, datetime.now(timezone.utc)
    db.flush()
    return data
