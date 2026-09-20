from __future__ import annotations

import uuid
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import Response
from sqlalchemy import select

from app.api.deps import DB, CurrentUser, client_ip
from app.core.errors import Unprocessable
from app.models import (
    Answer,
    AnswerSheet,
    Evaluation,
    EvaluationCriterion,
    Exam,
    ProcessingJob,
    Question,
    RubricCriterion,
    Student,
    StudentResult,
    TeacherOverride,
)
from app.models.enums import CourseRole, JobKind, SheetStatus
from app.schemas.jobs import JobOut
from app.schemas.results import (
    CriterionResultOut,
    QuestionMarkOut,
    ResultRowOut,
    ResultsOut,
    SheetResultOut,
    StudentExamOut,
    StudentProfileOut,
    TopicScoreOut,
    UnitResultOut,
)
from app.schemas.sheets import StudentOut
from app.services import access, analytics_service, audit, exam_state, export_service, jobs, results_service
from app.services.marks import q2

router = APIRouter(tags=["results"])


def _row(sheet: AnswerSheet, r: results_service.SheetResult, students: dict) -> ResultRowOut:
    st = students.get(sheet.student_id) if sheet.student_id else None
    return ResultRowOut(
        sheet_id=sheet.id, student=StudentOut.model_validate(st) if st else None, filename=sheet.original_filename, total=r.total, max_total=r.max_total,
        percentage=r.percentage, passed=r.passed, status=r.status, pending_reviews=r.pending_reviews + r.sheet_flags_open, unevaluated=r.unevaluated,
        questions=[
            QuestionMarkOut(label=u.unit.label, topic=u.unit.topic, score=u.final_score, max_marks=u.unit.max_marks, counted=u.counted,
                            evaluated=u.evaluated, overridden=u.overridden)
            for u in r.units
        ],
    )


@router.get("/exams/{exam_id}/results", response_model=ResultsOut)
def exam_results(exam_id: uuid.UUID, db: DB, user: CurrentUser):
    exam = access.get_exam(db, user, exam_id)
    sheets = list(db.scalars(select(AnswerSheet).where(
        AnswerSheet.exam_id == exam.id, AnswerSheet.deleted_at.is_(None), AnswerSheet.status == SheetStatus.EVALUATED).order_by(AnswerSheet.created_at)))
    students = {s.id: s for s in db.scalars(select(Student).where(Student.id.in_([s.student_id for s in sheets if s.student_id])))} if sheets else {}
    rows = [_row(sh, results_service.compute_sheet_result(db, sh.id), students) for sh in sheets]
    rows.sort(key=lambda r: (r.student.roll_number if r.student else "~", r.filename))
    return ResultsOut(exam_id=exam.id, exam_title=exam.title, max_total=exam_state.exam_total_for(db, exam),
                      pass_percentage=float(exam_state.pass_percentage(exam)), rows=rows)


@router.get("/answer-sheets/{sheet_id}/result", response_model=SheetResultOut)
def sheet_result(sheet_id: uuid.UUID, db: DB, user: CurrentUser):
    sheet, exam = access.get_sheet(db, user, sheet_id)
    r = results_service.compute_sheet_result(db, sheet.id)
    st = db.get(Student, sheet.student_id) if sheet.student_id else None
    base = _row(sheet, r, {st.id: st} if st else {})
    questions = {q.id: q for q in db.scalars(select(Question).where(Question.exam_id == exam.id))}
    answers = {a.id: a for a in db.scalars(select(Answer).where(Answer.answer_sheet_id == sheet.id))}
    evs = {e.answer_id: e for e in db.scalars(select(Evaluation).where(Evaluation.answer_id.in_(list(answers)), Evaluation.is_current))} if answers else {}
    ev_ids = [e.id for e in evs.values()]
    crits = list(db.scalars(select(EvaluationCriterion).where(EvaluationCriterion.evaluation_id.in_(ev_ids)).order_by(EvaluationCriterion.position))) if evs else []
    ovr = {o.evaluation_criterion_id: o for o in db.scalars(select(TeacherOverride).where(TeacherOverride.evaluation_id.in_(ev_ids), TeacherOverride.is_active))} if evs else {}
    rc = {c.id: c.title for c in db.scalars(select(RubricCriterion).where(RubricCriterion.id.in_([c.rubric_criterion_id for c in crits])))} if crits else {}
    units_out = []
    for u in r.units:
        q = questions[u.unit.question_id]
        text = next((s.text for s in q.subquestions if s.id == u.unit.subquestion_id), q.text) if u.unit.subquestion_id else q.text
        e = evs.get(u.answer_id) if u.answer_id else None
        crit_out = [
            CriterionResultOut(title=rc[c.rubric_criterion_id], max_score=c.max_score, ai_score=c.score, final_score=(ovr[c.id].final_score if c.id in ovr else c.score),
                               overridden=c.id in ovr, missing_points=c.missing_points, feedback=c.feedback)
            for c in crits if e and c.evaluation_id == e.id
        ]
        units_out.append(UnitResultOut(label=u.unit.label, topic=u.unit.topic, score=u.final_score, max_marks=u.unit.max_marks, counted=u.counted, evaluated=u.evaluated,
                                       overridden=u.overridden, answer_id=u.answer_id, question_text=text, ai_score=u.ai_score, criteria=crit_out))
    row = db.scalar(select(StudentResult).where(StudentResult.answer_sheet_id == sheet.id))
    fb = row.feedback if row else None
    job = db.scalar(select(ProcessingJob).where(ProcessingJob.kind == JobKind.GENERATE_FEEDBACK, ProcessingJob.entity_id == sheet.id)
                    .order_by(ProcessingJob.queued_at.desc()).limit(1))
    return SheetResultOut(
        **base.model_dump(), units=units_out,
        topics=[TopicScoreOut(topic=t, score=sc, max_score=mx, percentage=float(q2(sc / mx * 100)) if mx else 0.0) for t, (sc, mx) in sorted(r.topics.items())],
        feedback=fb, feedback_generated_at=row.feedback_generated_at if row else None,
        feedback_stale=bool(fb and abs(float(fb.get("generated_for_total", -1)) - float(r.total)) > 0.001),
        feedback_job=JobOut.model_validate(job) if job else None,
    )


@router.post("/answer-sheets/{sheet_id}/feedback", response_model=JobOut, status_code=202)
def generate_feedback(sheet_id: uuid.UUID, db: DB, user: CurrentUser):
    sheet, exam = access.get_sheet(db, user, sheet_id, CourseRole.INSTRUCTOR)
    if sheet.status != SheetStatus.EVALUATED:
        raise Unprocessable("Feedback can be generated once the sheet has been evaluated.", code="not_evaluated")
    job = jobs.enqueue(db, kind=JobKind.GENERATE_FEEDBACK, entity_type="answer_sheet", entity_id=sheet.id, course_id=exam.course_id, exam_id=exam.id, user_id=user.id)
    return JobOut.model_validate(job)


@router.get("/exams/{exam_id}/analytics")
def analytics(exam_id: uuid.UUID, db: DB, user: CurrentUser):
    exam = access.get_exam(db, user, exam_id)
    data = analytics_service.compute_and_store(db, exam)
    db.commit()
    return data


@router.get("/exams/{exam_id}/export.xlsx")
def export_xlsx(exam_id: uuid.UUID, request: Request, db: DB, user: CurrentUser):
    exam = access.get_exam(db, user, exam_id)
    data = export_service.build_workbook(db, exam)
    audit.record(db, actor_id=user.id, action="exam.export", entity_type="exam", entity_id=exam.id, course_id=exam.course_id, exam_id=exam.id, ip=client_ip(request))
    db.commit()
    safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in exam.title).strip() or "exam"
    return Response(
        content=data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote('MarksWala - ' + safe + '.xlsx')}", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/students/{student_id}", response_model=StudentProfileOut)
def student_profile(student_id: uuid.UUID, db: DB, user: CurrentUser):
    st = access.get_student(db, user, student_id)
    sheets = db.execute(
        select(AnswerSheet, Exam).join(Exam, Exam.id == AnswerSheet.exam_id)
        .where(AnswerSheet.student_id == st.id, AnswerSheet.deleted_at.is_(None), Exam.deleted_at.is_(None), AnswerSheet.status == SheetStatus.EVALUATED)
        .order_by(Exam.created_at)
    ).all()
    exams, weak_count = [], {}
    for sh, ex in sheets:
        r = results_service.compute_sheet_result(db, sh.id)
        weak = [t for t, (sc, mx) in r.topics.items() if mx and float(sc / mx * 100) < analytics_service.WEAK_BELOW]
        for t in weak:
            weak_count[t] = weak_count.get(t, 0) + 1
        exams.append(StudentExamOut(exam_id=ex.id, exam_title=ex.title, sheet_id=sh.id, total=r.total, max_total=r.max_total,
                                    percentage=r.percentage, status=r.status, weak_topics=weak))
    return StudentProfileOut(student=StudentOut.model_validate(st), course_id=st.course_id, exams=exams,
                             repeated_weak_topics=sorted(t for t, n in weak_count.items() if n >= 2))
