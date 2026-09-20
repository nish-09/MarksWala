"""Resource pipeline: upload -> storage -> extract -> chunk -> embed -> Qdrant -> verified COMPLETED."""
from __future__ import annotations

import pytest

from app.core.config import settings
from app.main import create_app
from app.providers.registry import get_vector_store
from tests.conftest import make_client, register
from tests.helpers import FIXTURES, upload, wait_for_job

COURSE = {"code": "CS201", "name": "Data Structures and Algorithms"}


@pytest.fixture
def course(client, teacher):
    return client.post("/api/courses", json=COURSE).json()


def _upload(client, course, name):
    r = upload(client, f"/api/courses/{course['id']}/resources", FIXTURES / "course" / name)
    assert r.status_code == 201, r.text
    return r.json()


def test_pdf_is_fully_processed_and_searchable(client, course, worker):
    res = _upload(client, course, "CS201_Lecture_Notes.pdf")
    assert res["status"] in ("UPLOADED", "PROCESSING") and res["job"]["status"] in ("QUEUED", "PROCESSING")
    job = wait_for_job(client, res["job"]["id"])
    assert job["status"] == "COMPLETED", job
    assert job["progress"] == 1.0 and job["started_at"] and job["completed_at"]

    done = client.get(f"/api/resources/{res['id']}").json()
    assert done["status"] == "COMPLETED" and done["chunk_count"] >= 8
    # the vectors truly exist in Qdrant, one per chunk
    assert get_vector_store().count_resource(done["id"]) == done["chunk_count"]

    chunks = client.get(f"/api/resources/{res['id']}/chunks?limit=200").json()
    assert len(chunks) == done["chunk_count"]
    assert all(c["page_number"] for c in chunks), "every PDF chunk must carry its page number"
    assert any(c["section"] and c["section"].startswith("Module 2") for c in chunks)
    binary = next(c for c in chunks if "n / 2^k = 1" in c["text"])
    assert binary["section"].startswith("Module 2")

    hits = client.post(f"/api/courses/{course['id']}/search", json={"query": "why does binary search need a sorted array", "top_k": 3}).json()
    assert hits and "binary search" in hits[0]["text"].lower()
    assert hits[0]["resource_title"] == done["title"] and hits[0]["page_number"] and hits[0]["score"] > 0.5


def test_pptx_chunks_carry_slide_numbers(client, course, worker):
    res = _upload(client, course, "CS201_Lecture_Slides.pptx")
    assert wait_for_job(client, res["job"]["id"])["status"] == "COMPLETED"
    chunks = client.get(f"/api/resources/{res['id']}/chunks?limit=200").json()
    assert {c["slide_number"] for c in chunks} >= {2, 3, 8}
    assert all(c["page_number"] is None for c in chunks)
    bfs = client.post(f"/api/courses/{course['id']}/search", json={"query": "level by level traversal using a queue"}).json()
    assert bfs[0]["slide_number"] in (5, 8) and "queue" in bfs[0]["text"].lower()


def test_duplicate_upload_is_rejected(client, course, worker):
    res = _upload(client, course, "CS201_Syllabus.pdf")
    wait_for_job(client, res["job"]["id"])
    r = upload(client, f"/api/courses/{course['id']}/resources", FIXTURES / "course" / "CS201_Syllabus.pdf")
    assert r.status_code == 409 and r.json()["error"]["code"] == "duplicate_resource"
    assert len(client.get(f"/api/courses/{course['id']}/resources").json()) == 1


def test_reprocess_does_not_duplicate_vectors(client, course, worker):
    res = _upload(client, course, "CS201_Syllabus.pdf")
    wait_for_job(client, res["job"]["id"])
    n = client.get(f"/api/resources/{res['id']}").json()["chunk_count"]
    r = client.post(f"/api/resources/{res['id']}/reprocess")
    assert r.status_code == 202
    assert wait_for_job(client, r.json()["job"]["id"])["status"] == "COMPLETED"
    again = client.get(f"/api/resources/{res['id']}").json()
    assert again["chunk_count"] == n and get_vector_store().count_resource(res["id"]) == n


def test_delete_removes_vectors_file_and_search_results(client, course, worker):
    res = _upload(client, course, "CS201_Syllabus.pdf")
    wait_for_job(client, res["job"]["id"])
    assert client.delete(f"/api/resources/{res['id']}").status_code == 200
    assert get_vector_store().count_resource(res["id"]) == 0
    assert client.get(f"/api/resources/{res['id']}").status_code == 404
    assert client.get(f"/api/resources/{res['id']}/file").status_code == 404


@pytest.mark.parametrize(
    "name,content,status,code",
    [
        ("empty.pdf", b"", 422, "empty_file"),
        ("broken.pdf", b"%PDF-1.4\nthis is not really a pdf at all", 422, "corrupt_pdf"),
        ("virus.pdf", b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 200, 415, "unsupported_file_type"),
        ("notes.ppt", b"\xd0\xcf\x11\xe0" + b"\x00" * 100, 415, "legacy_office_format"),
        ("renamed.docx", b"%PDF-1.4 ...", 415, None),  # content is PDF, extension says docx
        ("../../etc/passwd.txt", b"plain text with a hostile filename", 201, None),  # sanitized, never used as a path
    ],
)
def test_invalid_uploads_are_rejected_cleanly(client, course, name, content, status, code):
    r = upload(client, f"/api/courses/{course['id']}/resources", content, filename=name)
    assert r.status_code == status, r.text
    if code:
        assert r.json()["error"]["code"] == code
    if status == 201:
        f = r.json()["files"][0]
        assert "/" not in f["original_filename"] and ".." not in f["original_filename"]


def test_oversized_upload_rejected(client, course, monkeypatch):
    monkeypatch.setattr(settings, "max_resource_upload_mb", 1)
    r = upload(client, f"/api/courses/{course['id']}/resources", b"%PDF-1.4\n" + b"x" * (2 * 1024 * 1024), filename="big.pdf")
    assert r.status_code == 413 and r.json()["error"]["code"] == "file_too_large"


def test_resources_are_isolated_between_teachers(client, course, other_client, worker):
    res = _upload(client, course, "CS201_Syllabus.pdf")
    wait_for_job(client, res["job"]["id"])
    rid, cid = res["id"], course["id"]
    assert other_client.get(f"/api/resources/{rid}").status_code == 404
    assert other_client.get(f"/api/resources/{rid}/chunks").status_code == 404
    assert other_client.get(f"/api/resources/{rid}/file").status_code == 404
    assert other_client.delete(f"/api/resources/{rid}").status_code == 404
    assert other_client.post(f"/api/resources/{rid}/reprocess").status_code == 404
    assert other_client.get(f"/api/courses/{cid}/resources").status_code == 404
    assert other_client.post(f"/api/courses/{cid}/search", json={"query": "binary search"}).status_code == 404
    assert other_client.get(f"/api/jobs/{res['job']['id']}").status_code == 404
    assert other_client.get("/api/jobs").json() == []
    r = upload(other_client, f"/api/courses/{cid}/resources", FIXTURES / "course" / "CS201_Lecture_Notes.pdf")
    assert r.status_code == 404


def test_rag_search_never_crosses_courses(client, course, worker):
    """Material indexed in one teacher's course must never be retrievable from another teacher's course."""
    res = _upload(client, course, "CS201_Lecture_Notes.pdf")
    wait_for_job(client, res["job"]["id"])
    c2 = make_client(create_app())
    register(c2, name="Second")
    course2 = c2.post("/api/courses", json={"code": "OTHER", "name": "Other"}).json()
    hits = c2.post(f"/api/courses/{course2['id']}/search", json={"query": "binary search complexity", "top_k": 5}).json()
    assert hits == []
