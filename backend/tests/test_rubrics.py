"""Rubrics: balance enforcement, editing, approval, versioning, immutability, and grounded AI generation."""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.session import engine
from tests.helpers import FIXTURES, upload, wait_for_job

PAPER = {
    "questions": [
        {"number": 1, "text": "", "subquestions": [
            {"label": "a", "text": "Define a stack and list two applications.", "max_marks": 3},
            {"label": "b", "text": "Differentiate between a stack and a queue with suitable examples.", "max_marks": 4}]},
        {"number": 2, "text": "Explain the binary search algorithm. State its precondition and derive its time complexity.", "max_marks": 8, "topic": "Module 2: Searching and Sorting"},
    ]
}


@pytest.fixture
def exam(client, teacher):
    course = client.post("/api/courses", json={"code": "CS201", "name": "DSA"}).json()
    exam = client.post(f"/api/courses/{course['id']}/exams", json={"title": "IA1"}).json()
    client.put(f"/api/exams/{exam['id']}/questions", json=PAPER)
    assert client.post(f"/api/exams/{exam['id']}/questions/confirm").status_code == 200
    return exam


def units(client, exam_id):
    return client.get(f"/api/exams/{exam_id}/rubric").json()["units"]


def balanced_body(client, exam_id):
    out = []
    for u in units(client, exam_id):
        m = float(u["max_marks"])
        first = round(m / 2 * 2) / 2 if m > 1 else m
        crit = [{"title": "Concept", "max_marks": m - first}, {"title": "Explanation", "max_marks": first}] if m > 1 else [{"title": "Answer", "max_marks": m}]
        crit = [c for c in crit if c["max_marks"] > 0]
        out.append({"question_id": u["question_id"], "subquestion_id": u["subquestion_id"], "criteria": crit})
    return {"units": out}


def start_blank(client, exam_id):
    r = client.post(f"/api/exams/{exam_id}/rubric/versions")
    assert r.status_code == 201, r.text
    return r.json()


def test_manual_rubric_flow_and_balance_enforced(client, exam):
    eid = exam["id"]
    assert client.get(f"/api/exams/{eid}/rubric").json()["version"] is None
    r = start_blank(client, eid)
    assert r["version"]["version_number"] == 1 and r["version"]["status"] == "DRAFT" and r["editable"] and not r["all_balanced"]

    # Q2 criteria add to 7, not 8 -> cannot approve
    body = balanced_body(client, eid)
    body["units"][2]["criteria"][0]["max_marks"] -= 1
    edited = client.put(f"/api/exams/{eid}/rubric/draft", json=body).json()
    q2u = next(u for u in edited["units"] if u["label"] == "Q2")
    assert q2u["criteria_total"] == 7 and q2u["balanced"] is False and edited["all_balanced"] is False
    rej = client.post(f"/api/exams/{eid}/rubric/draft/approve")
    assert rej.status_code == 422 and rej.json()["error"]["code"] == "rubric_unbalanced"
    assert rej.json()["error"]["details"][0]["label"] == "Q2"

    # fix -> approve
    fixed = client.put(f"/api/exams/{eid}/rubric/draft", json=balanced_body(client, eid)).json()
    assert fixed["all_balanced"] is True
    ok = client.post(f"/api/exams/{eid}/rubric/draft/approve")
    assert ok.status_code == 200 and ok.json()["version"]["status"] == "APPROVED" and ok.json()["editable"] is False
    assert client.get(f"/api/exams/{eid}").json()["rubric_status"] == "APPROVED"


def test_teacher_can_add_delete_reorder_and_edit_criteria(client, exam):
    eid = exam["id"]
    start_blank(client, eid)
    u0 = units(client, eid)[2]  # Q2 (8 marks)
    crit = [
        {"title": "Third", "max_marks": 2, "description": "d3"},
        {"title": "First", "max_marks": 3, "expected_points": "sorted array"},
        {"title": "Second", "max_marks": 3},
    ]
    body = balanced_body(client, eid)
    body["units"][2]["criteria"] = crit
    out = client.put(f"/api/exams/{eid}/rubric/draft", json=body).json()
    q2 = next(u for u in out["units"] if u["label"] == "Q2")
    assert [c["title"] for c in q2["criteria"]] == ["Third", "First", "Second"] and q2["balanced"]  # order kept
    assert all(c["generation_confidence"] == 1.0 for c in q2["criteria"])  # teacher-authored
    # delete one and rebalance
    body["units"][2]["criteria"] = [{"id": q2["criteria"][1]["id"], "title": "First", "max_marks": 5, "expected_points": "sorted array"}, {"title": "Second", "max_marks": 3}]
    again = next(u for u in client.put(f"/api/exams/{eid}/rubric/draft", json=body).json()["units"] if u["label"] == "Q2")
    assert len(again["criteria"]) == 2 and again["balanced"] and again["criteria"][0]["id"] == q2["criteria"][1]["id"]  # ids kept


def test_invalid_criteria_rejected(client, exam):
    eid = exam["id"]
    start_blank(client, eid)
    body = balanced_body(client, eid)
    body["units"][0]["criteria"][0]["max_marks"] = 0
    assert client.put(f"/api/exams/{eid}/rubric/draft", json=body).status_code == 422
    body = balanced_body(client, eid)
    body["units"][0]["question_id"] = "00000000-0000-0000-0000-000000000000"
    assert client.put(f"/api/exams/{eid}/rubric/draft", json=body).json()["error"]["code"] == "unknown_question"
    body = balanced_body(client, eid)
    body["units"].append(body["units"][0])
    assert client.put(f"/api/exams/{eid}/rubric/draft", json=body).json()["error"]["code"] == "duplicate_unit"


def test_approved_versions_are_immutable_and_new_versions_supersede(client, exam):
    eid = exam["id"]
    start_blank(client, eid)
    client.put(f"/api/exams/{eid}/rubric/draft", json=balanced_body(client, eid))
    v1 = client.post(f"/api/exams/{eid}/rubric/draft/approve").json()["version"]

    # API: no draft -> cannot edit the approved rubric
    r = client.put(f"/api/exams/{eid}/rubric/draft", json=balanced_body(client, eid))
    assert r.status_code == 409 and r.json()["error"]["code"] == "no_draft"

    # DB: even direct SQL cannot touch an approved version's criteria (trigger)
    with engine.connect() as c:
        for stmt in ("UPDATE rubric_criteria SET max_marks = 1", "DELETE FROM rubric_criteria",
                     "INSERT INTO rubric_criteria (id, rubric_version_id, question_id, position, title, max_marks) "
                     "SELECT gen_random_uuid(), rubric_version_id, question_id, 99, 'sneaky', 1 FROM rubric_criteria LIMIT 1"):
            with pytest.raises(DBAPIError, match="frozen"):
                c.execute(text(stmt))
            c.rollback()

    # new version = editable copy; v1 untouched until v2 is approved
    v2 = client.post(f"/api/exams/{eid}/rubric/versions").json()
    assert v2["version"]["version_number"] == 2 and v2["version"]["status"] == "DRAFT"
    assert [c["title"] for u in v2["units"] for c in u["criteria"]] == [c["title"] for u in client.get(f"/api/exams/{eid}/rubric?version_id={v1['id']}").json()["units"] for c in u["criteria"]]
    body = balanced_body(client, eid)
    body["units"][2]["criteria"] = [{"title": "Everything", "max_marks": 8}]
    client.put(f"/api/exams/{eid}/rubric/draft", json=body)
    assert next(u for u in client.get(f"/api/exams/{eid}/rubric?version_id={v1['id']}").json()["units"] if u["label"] == "Q2")["criteria"][0]["title"] == "Concept"
    client.post(f"/api/exams/{eid}/rubric/draft/approve")
    versions = {v["version_number"]: v["status"] for v in client.get(f"/api/exams/{eid}/rubric").json()["versions"]}
    assert versions == {1: "SUPERSEDED", 2: "APPROVED"}
    assert client.post(f"/api/exams/{eid}/rubric/versions").status_code == 201  # and the cycle can continue
    assert client.post(f"/api/exams/{eid}/rubric/versions").status_code == 409  # but only one draft at a time


def test_structure_locked_after_approval(client, exam):
    eid = exam["id"]
    start_blank(client, eid)
    client.put(f"/api/exams/{eid}/rubric/draft", json=balanced_body(client, eid))
    client.post(f"/api/exams/{eid}/rubric/draft/approve")
    qs = client.get(f"/api/exams/{eid}/questions").json()
    assert qs["editable_structure"] is False
    edit = {"questions": [dict(id=q["id"], number=q["number"], text=q["text"], max_marks=q["max_marks"], topic=q["topic"],
                               subquestions=[dict(id=s["id"], label=s["label"], text=s["text"], max_marks=s["max_marks"]) for s in q["subquestions"]]) for q in qs["questions"]]}
    edit["questions"][1]["text"] += " (typo fixed)"
    assert client.put(f"/api/exams/{eid}/questions", json=edit).status_code == 200  # text edits stay possible
    edit["questions"][1]["max_marks"] = 9
    r = client.put(f"/api/exams/{eid}/questions", json=edit)
    assert r.status_code == 409 and r.json()["error"]["code"] == "structure_locked"
    edit["questions"].pop()
    assert client.put(f"/api/exams/{eid}/questions", json=edit).status_code == 409


def test_rubric_requires_confirmed_questions(client, teacher):
    course = client.post("/api/courses", json={"code": "X", "name": "X"}).json()
    exam = client.post(f"/api/courses/{course['id']}/exams", json={"title": "T"}).json()
    client.put(f"/api/exams/{exam['id']}/questions", json=PAPER)
    r = client.post(f"/api/exams/{exam['id']}/rubric/generate", json={})
    assert r.status_code == 422 and r.json()["error"]["code"] == "questions_not_confirmed"


def test_rubric_isolation_between_teachers(client, exam, other_client):
    eid = exam["id"]
    start_blank(client, eid)
    assert other_client.get(f"/api/exams/{eid}/rubric").status_code == 404
    assert other_client.post(f"/api/exams/{eid}/rubric/generate", json={}).status_code == 404
    assert other_client.put(f"/api/exams/{eid}/rubric/draft", json={"units": []}).status_code == 404
    assert other_client.post(f"/api/exams/{eid}/rubric/draft/approve").status_code == 404
    assert other_client.post(f"/api/exams/{eid}/rubric/versions").status_code == 404
    assert other_client.delete(f"/api/exams/{eid}/rubric/draft").status_code == 404


@pytest.mark.live_ai
def test_ai_rubric_is_grounded_balanced_and_regenerable(client, exam, worker):
    eid, cid = exam["id"], exam["course_id"]
    # index the real course notes first so retrieval has something to ground on
    r = upload(client, f"/api/courses/{cid}/resources", FIXTURES / "course" / "CS201_Lecture_Notes.pdf")
    assert wait_for_job(client, r.json()["job"]["id"])["status"] == "COMPLETED"

    gen = client.post(f"/api/exams/{eid}/rubric/generate", json={})
    assert gen.status_code == 202, gen.text
    job = wait_for_job(client, gen.json()["id"], timeout=300)
    assert job["status"] == "COMPLETED", job

    rub = client.get(f"/api/exams/{eid}/rubric").json()
    assert rub["version"]["status"] == "DRAFT" and rub["version"]["source"] == "AI" and rub["all_balanced"] is True
    for u in rub["units"]:
        assert 2 <= len(u["criteria"]) <= 6
        assert u["criteria_total"] == u["max_marks"], f"{u['label']} not balanced"  # the invariant
        assert all(0 < c["max_marks"] <= u["max_marks"] and c["generation_confidence"] is not None for c in u["criteria"])
    q2 = next(u for u in rub["units"] if u["label"] == "Q2")
    blob = " ".join(f"{c['title']} {c['description']} {c['expected_points']}" for c in q2["criteria"]).lower()
    assert "sorted" in blob and ("log" in blob or "halv" in blob), f"rubric is not grounded in the course notes: {blob[:400]}"

    # regenerate needs explicit consent because a draft exists
    assert client.post(f"/api/exams/{eid}/rubric/generate", json={}).json()["error"]["code"] == "draft_exists"

    # teacher approves the AI draft as-is
    ok = client.post(f"/api/exams/{eid}/rubric/draft/approve")
    assert ok.status_code == 200 and ok.json()["version"]["status"] == "APPROVED"
