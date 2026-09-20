"""Handwritten answer sheets: PDF -> pages -> OCR -> student -> segmentation -> question mapping (real Gemini OCR)."""
from __future__ import annotations

import pytest

from tests.helpers import FIXTURES, upload, wait_for_job

# the fixture paper: Q1(a,b) Q2 Q3(a,b) Q4|Q5
PAPER = {"questions": [
    {"number": 1, "text": "", "subquestions": [
        {"label": "a", "text": "Define a stack and list two of its applications.", "max_marks": 3},
        {"label": "b", "text": "Differentiate between a stack and a queue with suitable examples.", "max_marks": 4}]},
    {"number": 2, "text": "Explain the binary search algorithm. State its precondition and derive its time complexity.", "max_marks": 8},
    {"number": 3, "text": "", "subquestions": [
        {"label": "a", "text": "Write the algorithm for breadth first search (BFS) on a graph.", "max_marks": 4},
        {"label": "b", "text": "Compare BFS and DFS with respect to the data structure used, time complexity and use cases.", "max_marks": 5}]},
    {"number": 4, "text": "Explain how a singly linked list can be reversed. Give the algorithm and state its time and space complexity.", "max_marks": 8, "choice_group": "C4"},
    {"number": 5, "text": "Explain hashing. Describe any two collision resolution techniques.", "max_marks": 8, "choice_group": "C4"},
]}


@pytest.fixture
def exam(client, teacher):
    course = client.post("/api/courses", json={"code": "CS201", "name": "DSA"}).json()
    exam = client.post(f"/api/courses/{course['id']}/exams", json={"title": "IA1"}).json()
    assert client.put(f"/api/exams/{exam['id']}/questions", json=PAPER).status_code == 200
    assert client.post(f"/api/exams/{exam['id']}/questions/confirm").status_code == 200
    return exam


def upload_sheet(client, exam_id, student):
    r = upload(client, f"/api/exams/{exam_id}/answer-sheets", FIXTURES / "answer_sheets" / f"student_{student}.pdf", content_type="application/pdf")
    return r


def _files(paths):
    return [("files", (p.name, p.read_bytes(), "application/pdf")) for p in paths]


@pytest.mark.live_ai
def test_three_students_are_processed_identified_and_mapped_without_bleed(client, exam, worker):
    eid = exam["id"]
    paths = [FIXTURES / "answer_sheets" / f"student_{k}.pdf" for k in "ABC"]
    r = client.post(f"/api/exams/{eid}/answer-sheets", files=_files(paths))
    assert r.status_code == 201, r.text
    res = r.json()["results"]
    assert all(x["ok"] for x in res)
    jobs = {x["filename"]: x["sheet"]["job"]["id"] for x in res}
    for fn, jid in jobs.items():
        job = wait_for_job(client, jid, timeout=400)
        assert job["status"] in ("COMPLETED", "REQUIRES_REVIEW"), (fn, job)

    sheets = {s["original_filename"]: s for s in client.get(f"/api/exams/{eid}/answer-sheets").json()}
    detail = {k: client.get(f"/api/answer-sheets/{sheets[f'student_{k}.pdf']['id']}").json() for k in "ABC"}

    # -- identification --------------------------------------------------------------------------------------
    expect = {"A": ("CS2024-001", "Aarav Sharma"), "B": ("CS2024-002", "Bhavna Iyer"), "C": ("CS2024-003", "Chirag Patel")}
    for k, (roll, name) in expect.items():
        d = detail[k]
        assert d["status"] == "PROCESSED", d["error_message"]
        assert d["student"] and d["student"]["roll_number"] == roll and d["student"]["full_name"].lower() == name.lower(), (k, d["student"], d["detected_roll_number"])
        assert d["page_count"] == len(d["pages"]) >= 1
    assert len({d["student"]["id"] for d in detail.values()}) == 3

    # -- OCR fidelity + original preserved ---------------------------------------------------------------------
    a_text = " ".join(p["ocr_text"] for p in detail["A"]["pages"]).lower()
    assert "lifo" in a_text and "binary search" in a_text and "prev" in a_text
    assert all(0 < p["ocr_confidence"] <= 1 for p in detail["A"]["pages"])
    assert any(p["has_diagram"] for p in detail["A"]["pages"]), "student A drew a stack diagram"

    def ans(k, label):
        return next(a for a in detail[k]["answers"] if a["label"] == label)

    # -- mapping: right text under the right question, for every student -----------------------------------------
    assert "lifo" in ans("A", "Q1(a)")["original_text"].lower() and ans("A", "Q1(a)")["has_diagram"]
    assert "fifo" in ans("A", "Q1(b)")["original_text"].lower()
    assert "log" in ans("A", "Q2")["original_text"].lower() and "sorted" in ans("A", "Q2")["original_text"].lower()
    assert "queue" in ans("A", "Q3(a)")["original_text"].lower()
    assert "prev" in ans("A", "Q4")["original_text"].lower()
    assert ans("A", "Q5")["is_missing"] is True
    # Q1(a) of student A spans a page break?  the answer is continuous either way
    assert ans("B", "Q2")["original_text"].lower().count("binary") >= 1 and "bfs" not in ans("B", "Q2")["original_text"].lower()  # B wrote Q3 BEFORE Q2
    assert "queue" in ans("B", "Q3(a)")["original_text"].lower()
    assert ans("C", "Q4")["is_missing"] and ans("C", "Q3(b)")["is_missing"] and not ans("C", "Q5")["is_missing"]
    assert "chaining" in ans("C", "Q5")["original_text"].lower()
    assert "first in first out" in ans("C", "Q1(a)")["original_text"].lower()

    # -- isolation: no text from one student appears in another's answers ---------------------------------------
    assert "aarav" not in " ".join(a["original_text"] for a in detail["B"]["answers"]).lower()
    assert "chaining" not in " ".join(a["original_text"] for a in detail["A"]["answers"]).lower()
    for k in "ABC":
        assert not detail[k]["unassigned"], (k, detail[k]["unassigned"])
