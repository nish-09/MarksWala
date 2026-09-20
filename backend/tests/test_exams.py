"""Exams and the question hierarchy: manual editing, validation, confirmation, and AI parsing of a real paper."""
from __future__ import annotations

from decimal import Decimal

import pytest

from tests.helpers import FIXTURES, upload, wait_for_job


@pytest.fixture
def course(client, teacher):
    return client.post("/api/courses", json={"code": "CS201", "name": "DSA"}).json()


@pytest.fixture
def exam(client, course):
    r = client.post(f"/api/courses/{course['id']}/exams", json={"title": "Internal Assessment 1"})
    assert r.status_code == 201, r.text
    return r.json()


PAPER = {
    "questions": [
        {"number": 1, "text": "", "subquestions": [
            {"label": "a", "text": "Define a stack and list two applications.", "max_marks": 3},
            {"label": "b", "text": "Differentiate between a stack and a queue.", "max_marks": 4}]},
        {"number": 2, "text": "Explain binary search and derive its complexity.", "max_marks": 8, "topic": "Module 2"},
        {"number": 3, "text": "Explain reversing a linked list.", "max_marks": 8, "choice_group": "C3"},
        {"number": 4, "text": "Explain hashing.", "max_marks": 8, "choice_group": "C3"},
    ]
}


def test_create_and_list_exam(client, course, exam):
    assert exam["paper_status"] == "NONE" and exam["question_count"] == 0 and exam["rubric_status"] == "NONE"
    assert exam["pass_percentage"] == 40.0  # configured default
    assert exam["confidence_high_threshold"] == 0.85 and exam["confidence_review_threshold"] == 0.70
    assert [e["id"] for e in client.get(f"/api/courses/{course['id']}/exams").json()] == [exam["id"]]
    assert [e["id"] for e in client.get("/api/exams").json()] == [exam["id"]]


def test_thresholds_are_configurable_and_validated(client, course):
    ok = client.post(f"/api/courses/{course['id']}/exams", json={"title": "T", "confidence_high_threshold": 0.9, "confidence_review_threshold": 0.6, "pass_percentage": 50})
    assert ok.status_code == 201 and ok.json()["confidence_high_threshold"] == 0.9 and ok.json()["pass_percentage"] == 50
    bad = client.post(f"/api/courses/{course['id']}/exams", json={"title": "T", "confidence_high_threshold": 0.5, "confidence_review_threshold": 0.8})
    assert bad.status_code == 422
    eid = ok.json()["id"]
    assert client.patch(f"/api/exams/{eid}", json={"confidence_review_threshold": 0.95}).status_code == 422


def test_manual_questions_totals_and_choice(client, exam):
    r = client.put(f"/api/exams/{exam['id']}/questions", json=PAPER)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["computed_total_marks"] == 7 + 8 + 8  # Q3/Q4 are alternatives -> counted once
    q1 = body["questions"][0]
    assert q1["max_marks"] == 7 and [s["label"] for s in q1["subquestions"]] == ["a", "b"]
    assert body["questions"][2]["choice_group"] == body["questions"][3]["choice_group"] == "C3"
    assert client.get(f"/api/exams/{exam['id']}").json()["computed_total_marks"] == 23


def test_edit_preserves_ids_and_resets_confirmation(client, exam):
    body = client.put(f"/api/exams/{exam['id']}/questions", json=PAPER).json()
    assert client.post(f"/api/exams/{exam['id']}/questions/confirm").json()["confirmed"] is True
    ids = {q["number"]: q["id"] for q in body["questions"]}
    edited = {"questions": [dict(q, id=ids[q["number"]]) for q in PAPER["questions"]]}
    edited["questions"][1]["text"] = "Explain binary search, state its precondition and derive its complexity."
    again = client.put(f"/api/exams/{exam['id']}/questions", json=edited).json()
    assert {q["number"]: q["id"] for q in again["questions"]} == ids  # same rows, not recreated
    assert again["confirmed"] is True  # a text-only edit does not invalidate sign-off
    edited["questions"][1]["max_marks"] = 10
    changed = client.put(f"/api/exams/{exam['id']}/questions", json=edited).json()
    assert changed["confirmed"] is False and changed["computed_total_marks"] == 25


def test_invalid_structures_rejected(client, exam):
    dup = {"questions": [{"number": 1, "text": "a", "max_marks": 1}, {"number": 1, "text": "b", "max_marks": 1}]}
    assert client.put(f"/api/exams/{exam['id']}/questions", json=dup).json()["error"]["code"] == "duplicate_question_number"
    dup_sub = {"questions": [{"number": 1, "subquestions": [{"label": "a", "text": "x", "max_marks": 1}, {"label": "(a)", "text": "y", "max_marks": 1}]}]}
    assert client.put(f"/api/exams/{exam['id']}/questions", json=dup_sub).json()["error"]["code"] == "duplicate_label"
    assert client.put(f"/api/exams/{exam['id']}/questions", json={"questions": [{"number": 1, "text": " ", "max_marks": 2}]}).status_code == 422
    assert client.put(f"/api/exams/{exam['id']}/questions", json={"questions": [{"number": 1, "text": "x", "max_marks": -1}]}).status_code == 422


def test_cannot_confirm_with_missing_marks(client, exam):
    client.put(f"/api/exams/{exam['id']}/questions", json={"questions": [{"number": 1, "text": "Q with no marks", "max_marks": 0}]})
    r = client.post(f"/api/exams/{exam['id']}/questions/confirm")
    assert r.status_code == 422 and r.json()["error"]["code"] == "questions_invalid"
    assert client.get(f"/api/exams/{exam['id']}/questions").json()["confirmed"] is False


def test_swapping_question_numbers_works(client, exam):
    body = client.put(f"/api/exams/{exam['id']}/questions", json={"questions": [
        {"number": 1, "text": "first", "max_marks": 2}, {"number": 2, "text": "second", "max_marks": 3}]}).json()
    a, b = body["questions"][0]["id"], body["questions"][1]["id"]
    swapped = client.put(f"/api/exams/{exam['id']}/questions", json={"questions": [
        {"id": a, "number": 2, "text": "first", "max_marks": 2}, {"id": b, "number": 1, "text": "second", "max_marks": 3}]})
    assert swapped.status_code == 200, swapped.text
    assert [q["text"] for q in swapped.json()["questions"]] == ["second", "first"]


def test_exam_isolation_between_teachers(client, course, exam, other_client):
    eid = exam["id"]
    assert other_client.get(f"/api/exams/{eid}").status_code == 404
    assert other_client.patch(f"/api/exams/{eid}", json={"title": "x"}).status_code == 404
    assert other_client.delete(f"/api/exams/{eid}").status_code == 404
    assert other_client.get(f"/api/exams/{eid}/questions").status_code == 404
    assert other_client.put(f"/api/exams/{eid}/questions", json=PAPER).status_code == 404
    assert other_client.post(f"/api/exams/{eid}/questions/confirm").status_code == 404
    assert other_client.post(f"/api/courses/{course['id']}/exams", json={"title": "x"}).status_code == 404
    assert other_client.get("/api/exams").json() == []


@pytest.mark.live_ai
def test_real_question_paper_is_parsed_and_validated(client, exam, worker):
    r = upload(client, f"/api/exams/{exam['id']}/question-paper", FIXTURES / "exam" / "CS201_Internal_Assessment_1.pdf")
    assert r.status_code == 202, r.text
    assert r.json()["paper_status"] == "PROCESSING"
    job = wait_for_job(client, r.json()["paper_job"]["id"], timeout=240)
    assert job["status"] == "COMPLETED", job

    got = client.get(f"/api/exams/{exam['id']}").json()
    assert got["paper_status"] == "PARSED" and got["question_count"] == 5
    qs = client.get(f"/api/exams/{exam['id']}/questions").json()
    by_no = {q["number"]: q for q in qs["questions"]}
    assert [s["label"] for s in by_no[1]["subquestions"]] == ["a", "b"]
    assert [Decimal(str(s["max_marks"])) for s in by_no[1]["subquestions"]] == [3, 4]
    assert [Decimal(str(s["max_marks"])) for s in by_no[3]["subquestions"]] == [4, 5]
    assert by_no[2]["max_marks"] == 8 and by_no[2]["subquestions"] == []
    # internal choice: Q4 OR Q5 are alternatives of each other
    assert by_no[4]["choice_group"] and by_no[4]["choice_group"] == by_no[5]["choice_group"]
    assert by_no[1]["choice_group"] is None and by_no[2]["choice_group"] is None
    # totals come from arithmetic, and agree with what the paper states (32), not naive summation (40)
    assert qs["computed_total_marks"] == 32
    assert qs["declared_total_marks"] == 32 and qs["total_matches_declared"] is True
    assert not [i for i in qs["issues"] if i["level"] == "error"]
    assert client.post(f"/api/exams/{exam['id']}/questions/confirm").json()["confirmed"] is True


def test_corrupt_paper_rejected_synchronously(client, exam):
    r = upload(client, f"/api/exams/{exam['id']}/question-paper", b"%PDF-1.4 garbage", filename="paper.pdf")
    assert r.status_code == 422 and r.json()["error"]["code"] == "corrupt_pdf"
    assert client.get(f"/api/exams/{exam['id']}").json()["paper_status"] == "NONE"
