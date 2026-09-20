"""Database-level guarantees: triggers, constraints, foreign keys, rollback, duplicate/concurrent job protection."""
from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.core.errors import Conflict
from app.db.session import SessionLocal, session_scope
from app.models import (
    Answer,
    AnswerPage,
    AnswerSheet,
    Course,
    Evaluation,
    EvaluationCriterion,
    Exam,
    OcrResult,
    ProcessingJob,
    Question,
    Rubric,
    RubricCriterion,
    RubricVersion,
    Student,
    TeacherOverride,
    User,
)
from app.models.enums import (
    EvaluationStatus,
    JobKind,
    JobStatus,
    RubricSource,
    RubricVersionStatus,
    SheetStatus,
    TextSource,
)
from app.services import jobs


@pytest.fixture
def chain(db):
    """A minimal but complete record chain, from user down to a teacher override."""
    u = User(email=f"{uuid.uuid4().hex[:6]}@x.io", password_hash="x", full_name="T")
    db.add(u); db.flush()
    c = Course(owner_id=u.id, code="C", name="C"); db.add(c); db.flush()
    e = Exam(course_id=c.id, created_by=u.id, title="E"); db.add(e); db.flush()
    q = Question(exam_id=e.id, number=1, position=0, text="q", max_marks=Decimal(4)); db.add(q); db.flush()
    r = Rubric(exam_id=e.id); db.add(r); db.flush()
    v = RubricVersion(rubric_id=r.id, version_number=1, status=RubricVersionStatus.DRAFT, source=RubricSource.TEACHER); db.add(v); db.flush()
    rc = RubricCriterion(rubric_version_id=v.id, question_id=q.id, position=0, title="t", max_marks=Decimal(4)); db.add(rc); db.flush()
    st = Student(course_id=c.id, roll_number="R1", full_name="S"); db.add(st); db.flush()
    sh = AnswerSheet(exam_id=e.id, student_id=st.id, storage_key=f"k/{uuid.uuid4().hex}", original_filename="a.pdf", sha256="a" * 64, size_bytes=1, status=SheetStatus.PROCESSED)
    db.add(sh); db.flush()
    pg = AnswerPage(answer_sheet_id=sh.id, page_number=1, image_key=f"i/{uuid.uuid4().hex}", width=1, height=1); db.add(pg); db.flush()
    ocr = OcrResult(answer_page_id=pg.id, provider="p", model="m", text="original", confidence=0.9); db.add(ocr); db.flush()
    a = Answer(answer_sheet_id=sh.id, question_id=q.id, original_text="original"); db.add(a); db.flush()
    ev = Evaluation(answer_id=a.id, rubric_version_id=v.id, status=EvaluationStatus.COMPLETED, text_source=TextSource.ORIGINAL, answer_text_snapshot="original",
                    provider="p", model="m", prompt_version="v", ai_total=Decimal(3), max_total=Decimal(4))
    db.add(ev); db.flush()
    ec = EvaluationCriterion(evaluation_id=ev.id, rubric_criterion_id=rc.id, position=0, score=Decimal(3), max_score=Decimal(4), satisfied=False, partial=True, confidence=0.8)
    db.add(ec); db.flush()
    ov = TeacherOverride(evaluation_id=ev.id, evaluation_criterion_id=ec.id, ai_score=Decimal(3), teacher_score=Decimal(4), final_score=Decimal(4), reason="r", teacher_id=u.id)
    db.add(ov); db.commit()
    return dict(user=u, course=c, exam=e, q=q, version=v, rc=rc, student=st, sheet=sh, page=pg, ocr=ocr, answer=a, ev=ev, ec=ec, ov=ov)


def _fails(db, stmt: str, match: str | None = None, **params):
    with pytest.raises((DBAPIError, IntegrityError), match=match):
        db.execute(text(stmt), params)
        db.flush()
    db.rollback()


def test_original_ocr_text_cannot_be_rewritten(db, chain):
    _fails(db, "UPDATE ocr_results SET text='tampered' WHERE id=:i", "immutable", i=chain["ocr"].id)
    db.execute(text("UPDATE ocr_results SET has_diagram=true WHERE id=:i"), {"i": chain["ocr"].id})  # non-text metadata may change
    db.commit()


def test_ai_evaluation_is_immutable_except_the_current_flag(db, chain):
    _fails(db, "UPDATE evaluations SET ai_total=0 WHERE id=:i", "immutable", i=chain["ev"].id)
    _fails(db, "UPDATE evaluations SET answer_text_snapshot='x' WHERE id=:i", "immutable", i=chain["ev"].id)
    _fails(db, "UPDATE evaluation_criteria SET score=0 WHERE id=:i", "immutable", i=chain["ec"].id)
    db.execute(text("UPDATE evaluations SET is_current=false WHERE id=:i"), {"i": chain["ev"].id})
    db.commit()


def test_overrides_are_append_only(db, chain):
    _fails(db, "UPDATE teacher_overrides SET teacher_score=0 WHERE id=:i", "append-only", i=chain["ov"].id)
    _fails(db, "UPDATE teacher_overrides SET reason='x' WHERE id=:i", "append-only", i=chain["ov"].id)
    db.execute(text("UPDATE teacher_overrides SET is_active=false WHERE id=:i"), {"i": chain["ov"].id})  # only the flag may flip
    db.commit()


def test_audit_log_is_append_only(db, chain):
    from app.services import audit

    audit.record(db, actor_id=chain["user"].id, action="x", entity_type="t")
    db.commit()
    _fails(db, "UPDATE audit_logs SET action='forged'", "append-only")
    _fails(db, "DELETE FROM audit_logs", "append-only")


def test_check_constraints_reject_impossible_scores(db, chain):
    for stmt in [
        "INSERT INTO evaluation_criteria (id, evaluation_id, rubric_criterion_id, position, score, max_score, satisfied, partial, confidence) "
        "VALUES (gen_random_uuid(), :e, :r, 5, 9, 4, false, false, 0.5)",
        "INSERT INTO evaluation_criteria (id, evaluation_id, rubric_criterion_id, position, score, max_score, satisfied, partial, confidence) "
        "VALUES (gen_random_uuid(), :e, :r, 6, 1, 4, false, false, 1.5)",
    ]:
        _fails(db, stmt, e=chain["ev"].id, r=chain["rc"].id)
    _fails(db, "UPDATE questions SET max_marks=-1 WHERE id=:q", q=chain["q"].id)
    _fails(db, "INSERT INTO teacher_overrides (id, evaluation_id, evaluation_criterion_id, ai_score, teacher_score, final_score, reason, teacher_id, is_active) "
               "VALUES (gen_random_uuid(), :e, :c, 1, 1, 1, '   ', :t, false)", e=chain["ev"].id, c=chain["ec"].id, t=chain["user"].id)


def test_uniqueness_prevents_cross_student_and_duplicate_records(db, chain):
    c = chain
    # a second CURRENT evaluation for the same answer
    with pytest.raises(IntegrityError):
        db.add(Evaluation(answer_id=c["answer"].id, rubric_version_id=c["version"].id, attempt=2, is_current=True, status=EvaluationStatus.COMPLETED, text_source=TextSource.ORIGINAL,
                          answer_text_snapshot="x", provider="p", model="m", prompt_version="v", ai_total=Decimal(0), max_total=Decimal(4)))
        db.flush()
    db.rollback()
    # a second live sheet for the same student in one exam, and the same file twice
    for kw in [dict(student_id=c["student"].id, sha256="b" * 64), dict(student_id=None, sha256="a" * 64)]:
        with pytest.raises(IntegrityError):
            db.add(AnswerSheet(exam_id=c["exam"].id, storage_key=f"k/{uuid.uuid4().hex}", original_filename="d.pdf", size_bytes=1, **kw)); db.flush()
        db.rollback()
    # only one active override per criterion
    with pytest.raises(IntegrityError):
        db.add(TeacherOverride(evaluation_id=c["ev"].id, evaluation_criterion_id=c["ec"].id, ai_score=Decimal(3), teacher_score=Decimal(2), final_score=Decimal(2), reason="again", teacher_id=c["user"].id)); db.flush()
    db.rollback()
    # emails are unique case-insensitively
    with pytest.raises(IntegrityError):
        db.add(User(email=c["user"].email.upper(), password_hash="x", full_name="dup")); db.flush()
    db.rollback()


def test_foreign_keys_protect_graded_work(db, chain):
    _fails(db, "DELETE FROM questions WHERE id=:q", q=chain["q"].id)  # answers/criteria still reference it
    _fails(db, "INSERT INTO answers (id, answer_sheet_id, question_id, original_text, is_missing, has_diagram, created_at, updated_at) "
               "VALUES (gen_random_uuid(), gen_random_uuid(), :q, '', false, false, now(), now())", q=chain["q"].id)


def test_hard_delete_of_a_graded_exam_is_refused(db, chain):
    """The application soft-deletes exams; the database additionally refuses to hard-delete graded work."""
    _fails(db, "DELETE FROM exams WHERE id=:e", "violates foreign key", e=chain["exam"].id)
    assert db.execute(text("SELECT count(*) FROM evaluations")).scalar() == 1


def test_transaction_rolls_back_on_error():
    email = f"{uuid.uuid4().hex[:6]}@x.io"
    with pytest.raises(RuntimeError):
        with session_scope() as s:
            s.add(User(email=email, password_hash="x", full_name="ghost"))
            s.flush()
            raise RuntimeError("boom")
    with SessionLocal() as s:
        assert s.execute(text("SELECT count(*) FROM users WHERE email=:e"), {"e": email}).scalar() == 0


def _course_exam(db):
    u = User(email=f"{uuid.uuid4().hex[:6]}@x.io", password_hash="x", full_name="T"); db.add(u); db.flush()
    c = Course(owner_id=u.id, code="C", name="C"); db.add(c); db.flush()
    return u, c


def test_only_one_active_job_per_entity(db):
    u, c = _course_exam(db)
    eid = uuid.uuid4()
    j1 = jobs.create_job(db, kind=JobKind.PROCESS_RESOURCE, entity_type="resource", entity_id=eid, course_id=c.id, exam_id=None, user_id=u.id)
    db.commit()
    with pytest.raises(Conflict) as e:
        jobs.create_job(db, kind=JobKind.PROCESS_RESOURCE, entity_type="resource", entity_id=eid, course_id=c.id, exam_id=None, user_id=u.id)
    assert e.value.code == "job_already_active" and e.value.details["job_id"] == str(j1.id)
    db.rollback()
    # a different kind for the same entity is fine, and after the first finishes a new one is allowed
    jobs.create_job(db, kind=JobKind.GENERATE_FEEDBACK, entity_type="resource", entity_id=eid, course_id=c.id, exam_id=None, user_id=u.id)
    db.get(ProcessingJob, j1.id).status = JobStatus.COMPLETED
    db.commit()
    jobs.create_job(db, kind=JobKind.PROCESS_RESOURCE, entity_type="resource", entity_id=eid, course_id=c.id, exam_id=None, user_id=u.id)
    db.commit()


def test_concurrent_deliveries_run_a_job_exactly_once(db, monkeypatch):
    """Two workers receive the same message (at-least-once delivery): only one may claim and run it."""
    u, c = _course_exam(db)
    job = jobs.create_job(db, kind=JobKind.PROCESS_RESOURCE, entity_type="resource", entity_id=uuid.uuid4(), course_id=c.id, exam_id=None, user_id=u.id)
    db.commit()
    runs: list[int] = []
    gate = threading.Barrier(4)

    def fake(ctx):
        runs.append(1)
        import time; time.sleep(0.5)
        return jobs.JobResult()

    monkeypatch.setitem(jobs._HANDLERS, JobKind.PROCESS_RESOURCE, fake)

    def go():
        gate.wait()
        jobs.run_job(job.id)

    ts = [threading.Thread(target=go) for _ in range(4)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert len(runs) == 1
    with SessionLocal() as s:
        j = s.get(ProcessingJob, job.id)
        assert j.status == JobStatus.COMPLETED and j.attempts == 1


def test_reaper_recovers_crashed_and_lost_jobs(db, monkeypatch):
    u, c = _course_exam(db)
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    crashed = ProcessingJob(kind=JobKind.PROCESS_RESOURCE, entity_type="resource", entity_id=uuid.uuid4(), course_id=c.id, status=JobStatus.PROCESSING, attempts=1, max_attempts=3, started_at=old, heartbeat_at=old)
    exhausted = ProcessingJob(kind=JobKind.PROCESS_RESOURCE, entity_type="resource", entity_id=uuid.uuid4(), course_id=c.id, status=JobStatus.PROCESSING, attempts=3, max_attempts=3, started_at=old, heartbeat_at=old)
    lost = ProcessingJob(kind=JobKind.GENERATE_FEEDBACK, entity_type="answer_sheet", entity_id=uuid.uuid4(), course_id=c.id, status=JobStatus.QUEUED, queued_at=old)
    db.add_all([crashed, exhausted, lost]); db.commit()
    sent: list[uuid.UUID] = []
    monkeypatch.setattr(jobs, "dispatch", lambda jid, delay_ms=0: sent.append(jid))
    assert jobs.reap_stale_jobs() == 3
    with SessionLocal() as s:
        assert s.get(ProcessingJob, crashed.id).status == JobStatus.QUEUED
        f = s.get(ProcessingJob, exhausted.id)
        assert f.status == JobStatus.FAILED and f.error_code == "worker_crash"
    assert set(sent) == {crashed.id, lost.id}
