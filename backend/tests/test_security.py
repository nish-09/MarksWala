"""Security checks: storage confinement, injection strings, headers, cookies, production guards."""
from __future__ import annotations

import pytest
from sqlalchemy import text

from app.db.session import engine
from app.providers.storage import LocalStorage, StorageError
from tests.conftest import make_client, register


@pytest.mark.parametrize("key", ["../etc/passwd", "a/../../b", "/abs/path", "C:/windows/x", "a\\b", "a/b/../../..", "", "a b", "a/./b", "..", "a//b"])
def test_storage_rejects_traversal_and_odd_keys(tmp_path, key):
    s = LocalStorage(tmp_path)
    with pytest.raises(StorageError):
        s.save_bytes(key, b"x")
    with pytest.raises(StorageError):
        s.read_bytes(key)
    assert not any(tmp_path.parent.glob("etc"))


def test_storage_never_writes_outside_its_root(tmp_path):
    s = LocalStorage(tmp_path / "root")
    s.save_bytes("resources/abc/def.pdf", b"ok")
    assert (tmp_path / "root" / "resources" / "abc" / "def.pdf").read_bytes() == b"ok"
    assert s.exists("resources/abc/def.pdf") and not s.exists("resources/abc/nothing.pdf")


def test_sql_injection_strings_are_inert(client, teacher):
    evil = "x'); DROP TABLE users;--"
    r = client.post("/api/courses", json={"code": evil[:50], "name": evil})
    assert r.status_code == 201 and r.json()["name"] == evil
    with engine.connect() as c:
        assert c.execute(text("select count(*) from users")).scalar() >= 1
    login = make_client(client.app).post("/api/auth/login", json={"email": "a@b.co' OR '1'='1", "password": "x"})
    assert login.status_code == 422  # not even a valid email


def test_stored_markup_is_returned_verbatim_never_executed_server_side(client, teacher):
    xss = "<script>alert(1)</script>"
    course = client.post("/api/courses", json={"code": "XSS", "name": xss}).json()
    got = client.get(f"/api/courses/{course['id']}")
    assert got.json()["name"] == xss and got.headers["content-type"].startswith("application/json")
    assert got.headers["x-content-type-options"] == "nosniff"  # JSON is never sniffed into HTML


def test_security_headers_present(client):
    r = client.get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY" and r.headers["referrer-policy"] == "same-origin"
    assert r.headers.get("x-request-id")


def test_session_cookie_is_httponly_and_not_in_bodies(app):
    c = make_client(app)
    r = c.post("/api/auth/register", json={"email": "cookie@example.com", "password": "correct-horse-battery", "full_name": "C"})
    token = c.cookies.get("markswala_session")
    assert "httponly" in r.headers["set-cookie"].lower() and token and token not in r.text
    assert "password" not in r.text.lower()


def test_user_supplied_ids_cannot_impersonate(client, teacher, other_client):
    """The server derives identity from the session, never from a client-supplied user id."""
    other = other_client.get("/api/auth/me").json()
    r = client.post("/api/courses", json={"code": "IMP", "name": "x", "owner_id": other["id"], "user_id": other["id"]})
    assert r.status_code == 201 and r.json()["owner_id"] == teacher["id"]


def test_openapi_docs_are_disabled_in_production(monkeypatch):
    from app.core import config

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "x" * 40)
    monkeypatch.setenv("COOKIE_SECURE", "true")
    from app.main import create_app

    monkeypatch.setattr(config.settings, "app_env", "production")
    app = create_app()
    assert app.docs_url is None and app.openapi_url is None


@pytest.mark.parametrize("env,expect", [
    ({"APP_ENV": "production", "SECRET_KEY": "dev-only-insecure-secret-change-me", "COOKIE_SECURE": "true"}, "SECRET_KEY"),
    ({"APP_ENV": "production", "SECRET_KEY": "short", "COOKIE_SECURE": "true"}, "SECRET_KEY"),
    ({"APP_ENV": "production", "SECRET_KEY": "y" * 40, "COOKIE_SECURE": "false"}, "COOKIE_SECURE"),
])
def test_production_refuses_insecure_configuration(monkeypatch, env, expect):
    from app.core.config import Settings

    for k, v in env.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(ValueError, match=expect):
        Settings()


def test_registration_can_be_disabled(client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "allow_registration", False)
    r = client.post("/api/auth/register", json={"email": "no@example.com", "password": "correct-horse-battery", "full_name": "N"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "registration_disabled"


def test_weird_ids_and_pages_give_clean_404s_not_500s(client, teacher):
    for path in ["/api/courses/not-a-uuid", "/api/exams/00000000-0000-0000-0000-000000000000",
                 "/api/answer-sheets/00000000-0000-0000-0000-000000000000/pages/-1/image", "/api/resources/../../etc/passwd"]:
        assert client.get(path).status_code in (404, 422), path
