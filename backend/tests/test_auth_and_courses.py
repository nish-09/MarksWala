"""Authentication, session lifecycle, course CRUD and cross-teacher isolation."""
from __future__ import annotations

import pytest

from tests.conftest import PASSWORD, make_client, register


def test_register_login_logout_cycle(app):
    c = make_client(app)
    u = register(c, "Alice@Example.com", "Alice")
    assert u["email"] == "alice@example.com" and "password" not in str(u)
    assert c.get("/api/auth/me").json()["full_name"] == "Alice"

    assert c.post("/api/auth/logout").status_code == 200
    r = c.get("/api/auth/me")
    assert r.status_code == 401 and r.json()["error"]["code"] == "not_authenticated"

    r = c.post("/api/auth/login", json={"email": "alice@example.com", "password": PASSWORD})
    assert r.status_code == 200
    assert c.get("/api/auth/me").status_code == 200


def test_logout_really_revokes_the_server_side_session(app):
    c = make_client(app)
    register(c)
    stolen = c.cookies.get("markswala_session")
    c.post("/api/auth/logout")
    thief = make_client(app)
    thief.cookies.set("markswala_session", stolen)
    assert thief.get("/api/auth/me").status_code == 401  # a copied cookie is useless after logout


def test_session_cookie_flags(app):
    c = make_client(app)
    r = c.post("/api/auth/register", json={"email": "f@example.com", "password": PASSWORD, "full_name": "F"})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie


def test_duplicate_email_case_insensitive(client, teacher):
    r = client.post("/api/auth/register", json={"email": teacher["email"].upper(), "password": PASSWORD, "full_name": "Dup"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "email_taken"


@pytest.mark.parametrize("pw", ["short", "a" * 200])
def test_password_length_validated(client, pw):
    r = client.post("/api/auth/register", json={"email": "x@example.com", "password": pw, "full_name": "X"})
    assert r.status_code == 422


def test_wrong_password_is_generic_and_rate_limited(app, client, teacher):
    fresh = make_client(app)
    for _ in range(5):
        r = fresh.post("/api/auth/login", json={"email": teacher["email"], "password": "wrong-password-1"})
        assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_credentials"
    r = fresh.post("/api/auth/login", json={"email": teacher["email"], "password": PASSWORD})
    assert r.status_code == 429  # locked out even with the right password until the window passes
    # unknown email gives the same message as a wrong password (no account enumeration)
    other = make_client(app).post("/api/auth/login", json={"email": "nobody@example.com", "password": "x" * 12})
    assert other.status_code == 401 and other.json()["error"]["message"] == "Incorrect email or password."


def test_unauthenticated_requests_are_rejected(client):
    for path in ["/api/courses", "/api/auth/me"]:
        assert client.get(path).status_code == 401


def test_cross_origin_state_change_is_rejected(client, teacher):
    r = client.post("/api/courses", json={"code": "X", "name": "Evil"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "bad_origin"
    ok = client.post("/api/courses", json={"code": "X", "name": "Fine"}, headers={"Origin": "http://localhost:3000"})
    assert ok.status_code == 201


def test_course_crud(client, teacher):
    r = client.post("/api/courses", json={"code": "CS201", "name": "Data Structures and Algorithms", "description": "DSA"})
    assert r.status_code == 201
    course = r.json()
    assert course["my_role"] == "OWNER" and course["resource_count"] == 0

    assert client.post("/api/courses", json={"code": "cs201", "name": "Again"}).status_code == 409  # same code, case-insensitive
    assert client.post("/api/courses", json={"code": "  ", "name": "Blank"}).status_code == 422

    p = client.patch(f"/api/courses/{course['id']}", json={"name": "DSA (Fall)"})
    assert p.status_code == 200 and p.json()["name"] == "DSA (Fall)"
    assert [c["id"] for c in client.get("/api/courses").json()] == [course["id"]]

    assert client.delete(f"/api/courses/{course['id']}").status_code == 200
    assert client.get(f"/api/courses/{course['id']}").status_code == 404
    assert client.get("/api/courses").json() == []


def test_teacher_cannot_touch_another_teachers_course(client, teacher, other_client):
    """IDOR: every course route must answer 404 (not 403, not data) for a non-member."""
    course = client.post("/api/courses", json={"code": "PRIV", "name": "Private"}).json()
    cid = course["id"]
    assert other_client.get(f"/api/courses/{cid}").status_code == 404
    assert other_client.patch(f"/api/courses/{cid}", json={"name": "hacked"}).status_code == 404
    assert other_client.delete(f"/api/courses/{cid}").status_code == 404
    assert other_client.get(f"/api/courses/{cid}/members").status_code == 404
    assert other_client.post(f"/api/courses/{cid}/members", json={"email": "a@b.co"}).status_code == 404
    assert other_client.get("/api/courses").json() == []
    assert client.get(f"/api/courses/{cid}").json()["name"] == "Private"  # untouched


def test_member_roles_are_enforced(app, client, teacher):
    course = client.post("/api/courses", json={"code": "TEAM", "name": "Team course"}).json()
    helper = make_client(app)
    h = register(helper, name="Helper")
    r = client.post(f"/api/courses/{course['id']}/members", json={"email": h["email"], "role": "VIEWER"})
    assert r.status_code == 201
    # viewer can read but not modify
    assert helper.get(f"/api/courses/{course['id']}").json()["my_role"] == "VIEWER"
    assert helper.patch(f"/api/courses/{course['id']}", json={"name": "x"}).status_code == 403
    assert helper.delete(f"/api/courses/{course['id']}").status_code == 403
    assert client.post(f"/api/courses/{course['id']}/members", json={"email": h["email"]}).status_code == 409
    assert client.post(f"/api/courses/{course['id']}/members", json={"email": h["email"], "role": "OWNER"}).status_code == 422


def test_audit_trail_records_actions(client, teacher, db):
    from sqlalchemy import select

    from app.models import AuditLog

    client.post("/api/courses", json={"code": "AUD", "name": "Audited"})
    actions = [a.action for a in db.scalars(select(AuditLog).order_by(AuditLog.created_at))]
    assert "user.register" in actions and "course.create" in actions
