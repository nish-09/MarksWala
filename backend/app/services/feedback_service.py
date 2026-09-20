"""Student feedback grounded in the actual evaluation record.

The FACTS (which rubric criteria were missed, weak topics, which course sources cover them) are computed
deterministically from persisted data. The LLM only phrases them, and its output is validated against the facts:
an improvement must cite a criterion the student really missed, so feedback cannot degenerate into "study harder".
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.session import session_scope
from app.models import Answer, AnswerSheet, Evaluation, EvaluationCriterion, EvaluationSource, RubricCriterion, StudentResult, TeacherOverride
from app.models.enums import JobKind
from app.providers import registry
from app.services import results_service
from app.services.jobs import JobContext, JobError, JobResult, handler

log = logging.getLogger("MarksWala.feedback")
PROMPT_VERSION = "feedback-v1"
WEAK = 60.0


class Improvement(BaseModel):
    question: str = Field(description="The question label exactly as given, e.g. Q2 or Q3(b)")
    criterion: str = Field(description="The title of a MISSED criterion, copied exactly from the facts")
    advice: str = Field(description="Specific advice: what the student should have written and what to revise, citing a listed source when available")


class FeedbackOut(BaseModel):
    summary: str = Field(description="2-3 sentences on overall performance that mention concrete strengths and the main gaps")
    strengths: list[str] = Field(description="1-3 specific things the student did well, tied to criteria or topics in the facts")
    improvements: list[Improvement]


def build_facts(db: Session, sheet_id: uuid.UUID) -> dict:
    r = results_service.compute_sheet_result(db, sheet_id)
    answers = {a.id: a for a in db.scalars(select(Answer).where(Answer.answer_sheet_id == sheet_id))}
    evs = {e.answer_id: e for e in db.scalars(select(Evaluation).where(Evaluation.answer_id.in_(list(answers)), Evaluation.is_current))} if answers else {}
    crits = list(db.scalars(select(EvaluationCriterion).where(EvaluationCriterion.evaluation_id.in_([e.id for e in evs.values()])))) if evs else []
    ovr = {o.evaluation_criterion_id: o for o in db.scalars(select(TeacherOverride).where(TeacherOverride.evaluation_id.in_([e.id for e in evs.values()]), TeacherOverride.is_active))} if evs else {}
    rc = {c.id: c for c in db.scalars(select(RubricCriterion).where(RubricCriterion.id.in_([c.rubric_criterion_id for c in crits])))} if crits else {}
    srcs: dict[uuid.UUID, list[EvaluationSource]] = {}
    for s in db.scalars(select(EvaluationSource).where(EvaluationSource.evaluation_id.in_([e.id for e in evs.values()])).order_by(EvaluationSource.rank)) if evs else []:
        srcs.setdefault(s.evaluation_id, []).append(s)

    units, missed = [], []
    reading: dict[tuple, float] = {}
    for us in r.units:
        if not us.counted:
            continue
        e = evs.get(us.answer_id) if us.answer_id else None
        crit_rows = []
        for c in [c for c in crits if e and c.evaluation_id == e.id]:
            fin = ovr[c.id].final_score if c.id in ovr else c.score
            row = {"title": rc[c.rubric_criterion_id].title, "score": float(fin), "max": float(c.max_score), "missing": c.missing_points or "", "note": c.feedback or ""}
            crit_rows.append(row)
            if fin < c.max_score:
                missed.append({"question": us.unit.label, "topic": us.unit.topic, **row})
        pct = float(us.final_score / us.unit.max_marks * 100) if us.unit.max_marks else 0.0
        sources = [{"title": s.resource_title, "where": f"slide {s.slide_number}" if s.slide_number else f"page {s.page_number}" if s.page_number else "", "section": s.section}
                   for s in (srcs.get(e.id, [])[:2] if e else [])]
        units.append({"question": us.unit.label, "topic": us.unit.topic, "score": float(us.final_score), "max": float(us.unit.max_marks), "percentage": round(pct, 1),
                      "criteria": crit_rows, "sources": sources})
        if pct < WEAK:
            for s in sources:
                reading[(s["title"], s["where"], s["section"])] = reading.get((s["title"], s["where"], s["section"]), 0) + (float(us.unit.max_marks) - float(us.final_score))
    topics = [{"topic": t, "percentage": round(float(sc / mx * 100), 1) if mx else 0.0} for t, (sc, mx) in r.topics.items()]
    return {
        "total": float(r.total), "max_total": float(r.max_total), "percentage": float(r.percentage),
        "units": units, "missed_criteria": missed,
        "weak_topics": sorted([t for t in topics if t["percentage"] < WEAK], key=lambda t: t["percentage"]),
        "strong_topics": sorted([t for t in topics if t["percentage"] >= 80], key=lambda t: -t["percentage"]),
        "recommended_reading": [{"title": k[0], "where": k[1], "section": k[2]} for k, _ in sorted(reading.items(), key=lambda kv: -kv[1])][:6],
    }


def facts_prompt(f: dict) -> str:
    lines = [f"Total: {f['total']}/{f['max_total']} ({f['percentage']}%)", "", "QUESTIONS:"]
    for u in f["units"]:
        lines.append(f"- {u['question']} [{u['topic'] or 'no topic'}]: {u['score']}/{u['max']}")
        for c in u["criteria"]:
            state = "MISSED/PARTIAL" if c["score"] < c["max"] else "met"
            lines.append(f"    * criterion \"{c['title']}\" {c['score']}/{c['max']} ({state})" + (f" — missing: {c['missing']}" if c["missing"] and state != "met" else ""))
        if u["sources"] and u["percentage"] < WEAK:
            lines.append("    course sources: " + "; ".join(f"{s['title']} {s['where']}".strip() for s in u["sources"]))
    lines.append("\nWEAK TOPICS: " + (", ".join(f"{t['topic']} ({t['percentage']}%)" for t in f["weak_topics"]) or "none"))
    lines.append("STRONG TOPICS: " + (", ".join(t["topic"] for t in f["strong_topics"]) or "none"))
    return "\n".join(lines)


SYSTEM = (
    "You write feedback for ONE university student from their marked exam record. Use ONLY the facts provided. Be specific and kind: "
    "name the exact criteria they missed and what was missing, and say what to revise, citing the listed course sources by title and page/slide. "
    "Never give generic advice such as 'study harder'. Do not invent marks, criteria or sources. If nothing was missed, praise concretely and improvements is empty. "
    "Each improvement's `criterion` MUST be copied exactly from a criterion marked MISSED/PARTIAL."
)


def validate_feedback(out: FeedbackOut, facts: dict) -> None:
    missed = {(m["question"].lower(), m["title"].strip().lower()) for m in facts["missed_criteria"]}
    for imp in out.improvements:
        if (imp.question.strip().lower(), imp.criterion.strip().lower()) not in missed:
            raise ValueError(f"improvement cites '{imp.question} / {imp.criterion}', which the student did not miss")
    if facts["missed_criteria"] and not out.improvements:
        raise ValueError("the student missed criteria but no improvements were given")
    if not out.summary.strip():
        raise ValueError("empty summary")


def generate_feedback(db: Session, sheet_id: uuid.UUID) -> dict:
    facts = build_facts(db, sheet_id)
    ai = registry.get_grader()
    narrative: dict
    if not facts["missed_criteria"]:
        narrative = {"summary": f"Full marks: {facts['total']:g}/{facts['max_total']:g}. Every rubric criterion was met.", "strengths": [t["topic"] for t in facts["strong_topics"]][:3], "improvements": []}
        model = "deterministic"
    else:
        note = None
        out = None
        for _ in range(3):
            cand = ai.generate_structured(system=SYSTEM, schema=FeedbackOut, prompt=facts_prompt(facts) + (f"\n\nYour previous answer was rejected: {note}" if note else ""))
            try:
                validate_feedback(cand, facts)
                out = cand
                break
            except ValueError as e:
                note = str(e)
        if out is None:
            raise JobError("The AI feedback could not be validated against the marks. Try again.", code="feedback_invalid", retryable=True)
        narrative, model = out.model_dump(), ai.model
    return {"facts": facts, "narrative": narrative, "model": model, "prompt_version": PROMPT_VERSION,
            "generated_for_total": facts["total"], "generated_at": datetime.now(timezone.utc).isoformat()}


@handler(JobKind.GENERATE_FEEDBACK)
def feedback_job(ctx: JobContext) -> JobResult:
    sid = ctx.entity_id
    with session_scope() as db:
        sheet = db.get(AnswerSheet, sid)
        if sheet is None or sheet.deleted_at is not None:
            raise JobError("The answer sheet no longer exists.", code="sheet_missing")
        if sheet.status.value != "EVALUATED":
            raise JobError("Feedback can be generated after the sheet has been evaluated.", code="not_evaluated")
    ctx.progress(None, "Writing feedback from the evaluation record")
    with session_scope() as db:
        fb = generate_feedback(db, sid)
        results_service.recompute_sheet(db, sid)
        db.execute(update(StudentResult).where(StudentResult.answer_sheet_id == sid).values(feedback=fb, feedback_generated_at=datetime.now(timezone.utc)))
    return JobResult(message="Feedback generated")
