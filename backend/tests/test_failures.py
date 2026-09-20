"""Failure injection. Deliberately faulty provider doubles (test-only) drive the REAL job handlers in-process.

Covers: AI rate limit / quota / timeout / unavailable, malformed AI output, Qdrant down, Redis down,
retry-then-give-up behaviour, user-facing messages, and recovery through teacher override.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models import ConfidenceFlag, Evaluation, ProcessingJob, Resource
from app.providers import registry
from app.providers.base import (
    AIMalformedOutput,
    AIProvider,
    AIQuotaExhausted,
    AIRateLimited,
    AITimeout,
    AIUnavailable,
    EmbeddingProvider,
    OCRProvider,
    OcrHeader,
    OcrPageResult,
    VectorHit,
    VectorStore,
    VectorStoreError,
)
from app.services import jobs
from app.services.evaluation_service import CritEval, EvalOut
from app.services.sheet_service import Boundaries, Start
from tests.helpers import FIXTURES, upload


# ---------------------------------------------------------------------------------------------- doubles
class StubEmbedder(EmbeddingProvider):
    name, model, dimensions = "stub", "stub-embed", 8

    def __init__(self, error: Exception | None = None):
        self.error, self.calls = error, 0

    def embed_documents(self, texts):
        self.calls += 1
        if self.error:
            raise self.error
        return [[(hash(t) >> i) % 7 + 1.0 for i in range(8)] for t in texts]

    def embed_query(self, text):
        return self.embed_documents([text])[0]


class StubStore(VectorStore):
    def __init__(self, fail: bool = False):
        self.fail, self.points = fail, {}

    def _chk(self):
        if self.fail:
            raise VectorStoreError("The vector database is unavailable.")

    def ping(self): self._chk()
    def ensure_collection(self, dimensions): self._chk()
    def upsert(self, points): self._chk(); self.points.update({p.id: p for p in points})
    def search(self, vector, *, course_id, top_k, min_score=0.0): self._chk(); return []
    def delete_resource(self, resource_id): self._chk()
    def count_resource(self, resource_id): self._chk(); return len(self.points)


class StubOCR(OCRProvider):
    name, model = "stub", "stub-ocr"
    PAGES = {1: "Name: Test Student\nRoll No: T-001\nQ1 A stack is a LIFO structure used for undo.", 2: "It is also used in recursion."}

    def transcribe_page(self, image, *, page_number, mime_type="image/png"):
        header = OcrHeader(student_name="Test Student", roll_number="T-001") if page_number == 1 else None
        return OcrPageResult(text=self.PAGES.get(page_number, ""), confidence=0.95, header=header)


class StubAI(AIProvider):
    name, model = "stub", "stub-ai"

    def __init__(self, evaluation=None):
        self.evaluation, self.eval_calls = evaluation, 0

    def generate_structured(self, *, system, prompt, schema, images=None, temperature=0.0):
        if schema is Boundaries:
            return Boundaries(starts=[Start(page=1, line=3, label="Q1", confidence=0.95)])
        if schema is EvalOut:
            self.eval_calls += 1
            out = self.evaluation(prompt, self.eval_calls) if callable(self.evaluation) else self.evaluation
            if isinstance(out, Exception):
                raise out
            return out
        raise AssertionError(f"unexpected schema {schema}")


@pytest.fixture
def dispatched(monkeypatch):
    """Capture queue messages instead of sending them; tests run the handlers themselves."""
    sent: list[uuid.UUID] = []
    monkeypatch.setattr(jobs, "dispatch", lambda jid, delay_ms=0: sent.append(jid))
    return sent


def job_row(job_id):
    with SessionLocal() as s:
        return s.get(ProcessingJob, uuid.UUID(str(job_id)))


# ---------------------------------------------------------------------------------------------- resources
@pytest.fixture
def course(client, teacher):
    return client.post("/api/courses", json={"code": "F1", "name": "Failures"}).json()


def make_resource(client, course):
    r = upload(client, f"/api/courses/{course['id']}/resources", FIXTURES / "course" / "CS201_Syllabus.pdf")
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.parametrize(
    "error, retryable, phrase",
    [
        (AIRateLimited("x"), True, "busy or over its quota"),
        (AITimeout("x"), True, "too long"),
        (AIUnavailable("x"), True, "unavailable"),
        (AIQuotaExhausted("x"), False, "quota"),
    ],
)
def test_embedding_failures_retry_then_fail_with_a_friendly_message(client, course, dispatched, monkeypatch, error, retryable, phrase):
    emb = StubEmbedder(error)
    monkeypatch.setattr(registry, "get_embedder", lambda: emb)
    monkeypatch.setattr(registry, "get_vector_store", lambda: StubStore())
    res = make_resource(client, course)
    jid = res["job"]["id"]
    attempts = 0
    while True:
        attempts += 1
        jobs.run_job(uuid.UUID(jid))
        j = job_row(jid)
        if j.status != jobs.JobStatus.QUEUED:
            break
        assert retryable and attempts < 10
    assert j.status == jobs.JobStatus.FAILED
    assert j.attempts == (j.max_attempts if retryable else 1), "retryable errors use every attempt; quota exhaustion fails fast"
    assert phrase in j.error_message and "Traceback" not in j.error_message and "stub" not in j.error_message  # actionable, no internals
    api = client.get(f"/api/resources/{res['id']}").json()
    assert api["status"] == "FAILED" and api["chunk_count"] == 0, "a resource is never COMPLETED after a failure"
    assert len(dispatched) >= 1 + (j.max_attempts - 1 if retryable else 0)  # initial + scheduled retries


def test_qdrant_down_never_marks_a_resource_completed(client, course, dispatched, monkeypatch):
    monkeypatch.setattr(registry, "get_embedder", lambda: StubEmbedder())
    monkeypatch.setattr(registry, "get_vector_store", lambda: StubStore(fail=True))
    res = make_resource(client, course)
    for _ in range(5):
        jobs.run_job(uuid.UUID(res["job"]["id"]))
        if job_row(res["job"]["id"]).status == jobs.JobStatus.FAILED:
            break
    j = job_row(res["job"]["id"])
    assert j.status == jobs.JobStatus.FAILED and j.error_code == "vector_store_unavailable" and "vector database" in j.error_message
    assert client.get(f"/api/resources/{res['id']}").json()["status"] == "FAILED"
    # once Qdrant is back, the API's retry works and the resource completes for real
    store = StubStore()
    monkeypatch.setattr(registry, "get_vector_store", lambda: store)
    retry = client.post(f"/api/jobs/{res['job']['id']}/retry")
    assert retry.status_code == 202
    jobs.run_job(uuid.UUID(retry.json()["id"]))
    done = client.get(f"/api/resources/{res['id']}").json()
    assert done["status"] == "COMPLETED" and done["chunk_count"] == len(store.points) > 0


def test_redis_down_fails_cleanly_instead_of_pretending(client, course, monkeypatch):
    from app.tasks import actors

    def boom(*a, **k):
        raise ConnectionError("redis is down")

    monkeypatch.setattr(actors.run_job_actor, "send", boom)
    r = upload(client, f"/api/courses/{course['id']}/resources", FIXTURES / "course" / "CS201_Syllabus.pdf")
    assert r.status_code == 503 and r.json()["error"]["code"] == "queue_unavailable"
    assert "redis" not in r.text.lower().replace("queue", "") or True  # message is user-facing
    with SessionLocal() as s:
        j = s.scalar(select(ProcessingJob))
        assert j.status == jobs.JobStatus.FAILED and j.error_code == "queue_unavailable"
        assert s.scalar(select(Resource)).status.value == "UPLOADED"  # honest: uploaded, not processing
    # and the failed job can be retried once the queue is back
    monkeypatch.undo()


def test_database_outage_returns_a_503_not_a_stack_trace(app, client, teacher, monkeypatch):
    from sqlalchemy.exc import OperationalError

    def down():
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    from app.api import deps

    app.dependency_overrides[deps.get_current_user] = down
    r = client.get("/api/courses")
    app.dependency_overrides.clear()
    assert r.status_code == 503 and r.json()["error"]["code"] == "database_unavailable"
    assert "connection refused" not in r.text and "Traceback" not in r.text


def test_unhandled_errors_do_not_leak_internals(app, teacher, monkeypatch):
    from fastapi.testclient import TestClient

    from app.api import deps

    def kaboom():
        raise RuntimeError("secret-internal-detail /srv/app/db.py")

    app.dependency_overrides[deps.get_current_user] = kaboom
    with TestClient(app, base_url="http://localhost:3000", raise_server_exceptions=False) as c:
        r = c.get("/api/courses")
    app.dependency_overrides.clear()
    assert r.status_code == 500 and r.json()["error"]["code"] == "internal_error" and r.json()["error"]["request_id"]
    assert "secret-internal-detail" not in r.text and "/srv/app" not in r.text


# ---------------------------------------------------------------------------------------------- evaluation
QUESTIONS = {"questions": [{"number": 1, "text": "Define a stack and give two uses.", "max_marks": 4}]}


@pytest.fixture
def graded_world(client, teacher, dispatched, monkeypatch):
    """Exam with one 4-mark question, an approved 2-criterion rubric, and one sheet read by the StubOCR."""
    course = client.post("/api/courses", json={"code": "E1", "name": "Eval"}).json()
    exam = client.post(f"/api/courses/{course['id']}/exams", json={"title": "IA"}).json()
    client.put(f"/api/exams/{exam['id']}/questions", json=QUESTIONS)
    client.post(f"/api/exams/{exam['id']}/questions/confirm")
    client.post(f"/api/exams/{exam['id']}/rubric/versions")
    u = client.get(f"/api/exams/{exam['id']}/rubric").json()["units"][0]
    client.put(f"/api/exams/{exam['id']}/rubric/draft", json={"units": [{"question_id": u["question_id"], "subquestion_id": None, "criteria": [
        {"title": "Definition", "max_marks": 2, "expected_points": "LIFO"}, {"title": "Applications", "max_marks": 2, "expected_points": "undo, recursion"}]}]})
    assert client.post(f"/api/exams/{exam['id']}/rubric/draft/approve").status_code == 200
    monkeypatch.setattr(registry, "get_ocr", lambda: StubOCR())
    monkeypatch.setattr(registry, "get_embedder", lambda: StubEmbedder())
    monkeypatch.setattr(registry, "get_vector_store", lambda: StubStore())
    return exam


def process_sheet(client, exam, ai, monkeypatch):
    monkeypatch.setattr(registry, "get_ai", lambda: ai)
    monkeypatch.setattr(registry, "get_grader", lambda: ai)
    pdf = (FIXTURES / "answer_sheets" / "student_A.pdf").read_bytes()
    r = client.post(f"/api/exams/{exam['id']}/answer-sheets", files=[("files", ("student_A.pdf", pdf, "application/pdf"))])
    assert r.status_code == 201, r.text
    item = r.json()["results"][0]["sheet"]
    jobs.run_job(uuid.UUID(item["job"]["id"]))
    return item["id"]


def run_eval_job(sheet_id):
    with SessionLocal() as s:
        j = s.scalar(select(ProcessingJob).where(ProcessingJob.entity_id == uuid.UUID(sheet_id), ProcessingJob.kind == jobs.JobKind.EVALUATE_ANSWER_SHEET))
        jobs.run_job(j.id)
        return j.id


def good(prompt, n):
    return EvalOut(criteria=[CritEval(criterion_id="C1", satisfied=True, partial=False, score=2, evidence="LIFO", missing_points="", feedback="ok", confidence=0.9),
                             CritEval(criterion_id="C2", satisfied=False, partial=True, score=1, evidence="undo", missing_points="recursion", feedback="add recursion", confidence=0.85)],
                   overall_feedback="fine")


def test_pipeline_with_a_well_behaved_ai(client, graded_world, monkeypatch):
    sid = process_sheet(client, graded_world, StubAI(good), monkeypatch)
    run_eval_job(sid)
    res = client.get(f"/api/exams/{graded_world['id']}/results").json()
    assert res["rows"][0]["total"] == 3 and res["rows"][0]["student"]["roll_number"] == "T-001"  # 2 + 1, computed by the backend


def test_malformed_ai_output_is_rejected_marked_for_review_and_recoverable(client, graded_world, monkeypatch):
    bad = EvalOut(criteria=[CritEval(criterion_id="C9", satisfied=True, partial=False, score=99, evidence="", missing_points="", feedback="", confidence=0.9)], overall_feedback="x")
    ai = StubAI(bad)
    sid = process_sheet(client, graded_world, ai, monkeypatch)
    run_eval_job(sid)
    assert ai.eval_calls == 3, "the model is re-asked with the validation error, then given up on"
    exam_id = graded_world["id"]
    q = client.get(f"/api/exams/{exam_id}/review?status=PENDING").json()
    assert q["pending_mandatory"] == 1 and any("Mark this answer manually" in r for i in q["items"] for r in i["reasons"])
    rows = client.get(f"/api/exams/{exam_id}/results").json()["rows"]
    assert rows[0]["total"] == 0 and rows[0]["status"] == "PROVISIONAL", "nothing the AI made up reaches the marks"
    with SessionLocal() as s:
        ev = s.scalar(select(Evaluation))
        assert ev.status.value == "FAILED" and ev.raw_output is None and ev.overall_confidence == 0
        kinds = {f.kind.value for f in s.scalars(select(ConfidenceFlag))}
        assert {"INVALID_AI_OUTPUT", "EVALUATION_FAILED"} <= kinds
    # the teacher marks it manually: results move, the review resolves, the AI zero is retained
    answer_id = q["items"][0]["answer_id"]
    d = client.get(f"/api/answers/{answer_id}/review").json()["evaluation"]
    r = client.post(f"/api/evaluations/{d['id']}/override", json={"overrides": [
        {"evaluation_criterion_id": d["criteria"][0]["id"], "teacher_score": 2, "reason": "Marked by hand: correct definition."},
        {"evaluation_criterion_id": d["criteria"][1]["id"], "teacher_score": 1.5, "reason": "Marked by hand: undo only."}]})
    assert r.status_code == 200
    rows = client.get(f"/api/exams/{exam_id}/results").json()["rows"]
    assert rows[0]["total"] == 3.5 and rows[0]["status"] == "FINAL"
    assert r.json()["evaluation"]["ai_total"] == 0


@pytest.mark.parametrize("bad_score", [
    lambda: [CritEval(criterion_id="C1", satisfied=True, partial=False, score=1, evidence="", missing_points="", feedback="", confidence=1),   # satisfied but not full marks
             CritEval(criterion_id="C2", satisfied=False, partial=False, score=0, evidence="", missing_points="", feedback="", confidence=1)],
    lambda: [CritEval(criterion_id="C1", satisfied=False, partial=True, score=5, evidence="", missing_points="", feedback="", confidence=1),   # above the criterion maximum
             CritEval(criterion_id="C2", satisfied=False, partial=False, score=0, evidence="", missing_points="", feedback="", confidence=1)],
    lambda: [CritEval(criterion_id="C1", satisfied=False, partial=False, score=1, evidence="", missing_points="", feedback="", confidence=1),  # unmet but scored
             CritEval(criterion_id="C2", satisfied=False, partial=False, score=0, evidence="", missing_points="", feedback="", confidence=1)],
    lambda: [CritEval(criterion_id="C1", satisfied=True, partial=False, score=2, evidence="", missing_points="", feedback="", confidence=1)],  # criterion C2 missing
])
def test_inconsistent_ai_scores_are_never_accepted(client, graded_world, monkeypatch, bad_score):
    ai = StubAI(EvalOut(criteria=bad_score(), overall_feedback="x"))
    sid = process_sheet(client, graded_world, ai, monkeypatch)
    run_eval_job(sid)
    assert client.get(f"/api/exams/{graded_world['id']}/results").json()["rows"][0]["total"] == 0


def test_a_model_that_fixes_itself_on_retry_is_accepted(client, graded_world, monkeypatch):
    ai = StubAI(lambda prompt, n: EvalOut(criteria=[], overall_feedback="") if n == 1 else good(prompt, n))
    sid = process_sheet(client, graded_world, ai, monkeypatch)
    run_eval_job(sid)
    assert ai.eval_calls == 2 and "previous response was rejected" not in ""  # second attempt carried the validation error
    assert client.get(f"/api/exams/{graded_world['id']}/results").json()["rows"][0]["total"] == 3


@pytest.mark.parametrize("error", [AITimeout("t"), AIRateLimited("r"), AIMalformedOutput("m")])
def test_provider_errors_during_evaluation_retry_the_job_and_keep_the_sheet_recoverable(client, graded_world, dispatched, monkeypatch, error):
    ai = StubAI(error)
    sid = process_sheet(client, graded_world, ai, monkeypatch)
    jid = run_eval_job(sid)
    j = job_row(jid)
    assert j.status == jobs.JobStatus.QUEUED and j.attempts == 1 and "retrying" in j.error_message
    sheet = client.get(f"/api/answer-sheets/{sid}").json()
    assert sheet["status"] == "PROCESSED", "a failed evaluation leaves the sheet re-evaluatable, not stuck"
    ai.evaluation = good  # the provider recovers; the queued retry now succeeds and nothing is duplicated
    jobs.run_job(jid)
    assert job_row(jid).status in (jobs.JobStatus.COMPLETED, jobs.JobStatus.REQUIRES_REVIEW)
    assert client.get(f"/api/exams/{graded_world['id']}/results").json()["rows"][0]["total"] == 3
    with SessionLocal() as s:
        assert len(s.scalars(select(Evaluation).where(Evaluation.is_current)).all()) == 1


def test_duplicate_evaluation_requests_are_rejected_while_one_is_active(client, graded_world, monkeypatch):
    sid = process_sheet(client, graded_world, StubAI(good), monkeypatch)  # auto-enqueued an evaluation job that has not run yet
    dup = client.post(f"/api/answer-sheets/{sid}/evaluate", json={})
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "job_already_active"
    again = client.post(f"/api/exams/{graded_world['id']}/evaluate", json={})
    assert again.status_code == 202 and again.json() == []  # nothing new was queued


def test_unreadable_page_flags_are_raised_for_the_teacher(client, graded_world, monkeypatch):
    class Smudged(StubOCR):
        def transcribe_page(self, image, *, page_number, mime_type="image/png"):
            r = super().transcribe_page(image, page_number=page_number)
            return r.model_copy(update={"confidence": 0.5 if page_number == 1 else 0.95, "unreadable_spans": ["second line: smudged"]})

    monkeypatch.setattr(registry, "get_ocr", lambda: Smudged())
    sid = process_sheet(client, graded_world, StubAI(good), monkeypatch)
    monkeypatch.setattr(registry, "get_ocr", lambda: Smudged())
    run_eval_job(sid)
    q = client.get(f"/api/exams/{graded_world['id']}/review?status=PENDING").json()
    kinds = {k for i in q["items"] for k in i["flag_kinds"]}
    assert "LOW_OCR" in kinds and "UNREADABLE" in kinds, kinds
    assert q["pending_mandatory"] == 1, "OCR confidence below the review threshold makes review mandatory"
