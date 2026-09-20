"""Test harness. Runs against REAL PostgreSQL, Redis and Qdrant (no in-memory substitutes).

Isolation: a dedicated database, Redis DB index, Qdrant collection and storage directory.
"""
from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

# --- must be set before the app is imported -----------------------------------------------------
_TEST_DB = os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://marks:marks@127.0.0.1:5432/markswala_test")
os.environ["DATABASE_URL"] = _TEST_DB
os.environ["APP_ENV"] = "test"
os.environ["REDIS_URL"] = os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:6379/1")
os.environ["QDRANT_COLLECTION"] = "markswala_test_chunks"
os.environ["STORAGE_LOCAL_ROOT"] = tempfile.mkdtemp(prefix="markswala-test-storage-")
os.environ["LOGIN_RATE_LIMIT_ATTEMPTS"] = "5"
# Replay previously recorded REAL provider responses (keyed by full prompt+images) to conserve API quota.
# Set MARKSWALA_NO_AI_CACHE=1 for a genuine live run (the final acceptance run does).
if not os.environ.get("MARKSWALA_NO_AI_CACHE"):
    os.environ["AI_RESPONSE_CACHE_DIR"] = str(Path(__file__).resolve().parents[2] / ".ai-cache")
else:
    os.environ.pop("AI_RESPONSE_CACHE_DIR", None)

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.session import SessionLocal, engine

BACKEND = Path(__file__).resolve().parents[1]
PASSWORD = "correct-horse-battery"


@pytest.fixture(scope="session", autouse=True)
def _migrated_database():
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    with engine.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    command.upgrade(cfg, "head")
    yield


@pytest.fixture(autouse=True)
def _clean_state():
    with engine.begin() as c:
        tables = [
            r[0]
            for r in c.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'alembic_version'")
            )
        ]
        # audit_logs is append-only via trigger; drop the guard only for test cleanup
        c.execute(text("ALTER TABLE audit_logs DISABLE TRIGGER trg_audit_logs_append_only"))
        c.execute(text("TRUNCATE " + ", ".join(f'"{t}"' for t in tables) + " RESTART IDENTITY CASCADE"))
        c.execute(text("ALTER TABLE audit_logs ENABLE TRIGGER trg_audit_logs_append_only"))
    from app.services.ratelimit import get_redis

    try:
        get_redis().flushdb()
    except Exception:
        pass
    yield


@pytest.fixture
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


@pytest.fixture
def app():
    from app.main import create_app

    return create_app()


@pytest.fixture
def client(app):
    with TestClient(app, base_url="http://localhost:3000") as c:
        yield c


def make_client(app) -> TestClient:
    """An independent cookie jar (a second browser)."""
    return TestClient(app, base_url="http://localhost:3000")


def register(client: TestClient, email: str | None = None, name: str = "Test Teacher") -> dict:
    email = email or f"teacher-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post("/api/auth/register", json={"email": email, "password": PASSWORD, "full_name": name})
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def teacher(client):
    return register(client)


@pytest.fixture
def other_client(app):
    c = make_client(app)
    register(c, name="Other Teacher")
    yield c
    c.close()


# --- real background worker for pipeline tests -------------------------------------------------------
from tests.helpers import FIXTURES, start_worker, stop_worker  # noqa: E402,F401


@pytest.fixture(scope="session")
def worker():
    """A real Dramatiq worker process against the test database / Redis DB / Qdrant collection."""
    from app.providers.registry import get_vector_store

    store = get_vector_store()
    try:
        store.client.delete_collection(store.collection)
    except Exception:
        pass
    proc, log = start_worker()
    yield proc
    stop_worker(proc, log)
