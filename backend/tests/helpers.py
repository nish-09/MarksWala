"""Shared helpers for pipeline tests: a real Dramatiq worker subprocess and job polling."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
FIXTURES = REPO / "fixtures"


def start_worker(threads: int = 4) -> tuple[subprocess.Popen, object]:
    log = open(BACKEND / "tests" / ".worker.log", "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "dramatiq", "app.tasks.actors", "--processes", "1", "--threads", str(threads)],
        cwd=str(BACKEND), env=os.environ.copy(), stdout=log, stderr=subprocess.STDOUT,
    )
    time.sleep(4)
    assert proc.poll() is None, "worker failed to start; see tests/.worker.log"
    return proc, log


def stop_worker(proc: subprocess.Popen, log) -> None:
    """Kill the whole process tree: on Windows Dramatiq's spawned child otherwise outlives the parent
    and keeps consuming jobs from the queue with stale code."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
    log.close()


def wait_for_job(client: TestClient, job_id: str, *, timeout: float = 180, until=("COMPLETED", "FAILED", "REQUIRES_REVIEW")) -> dict:
    deadline = time.time() + timeout
    j: dict = {}
    while time.time() < deadline:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j.get("status") in until:
            if j["status"] == "FAILED":
                print("JOB FAILED:", j.get("error_code"), j.get("error_message"))
            return j
        time.sleep(0.7)
    raise AssertionError(f"job {job_id} did not finish in {timeout}s; last state: {j}")


def upload(client: TestClient, url: str, path_or_bytes, filename: str | None = None, content_type: str = "application/octet-stream", **data):
    if isinstance(path_or_bytes, (str, Path)):
        p = Path(path_or_bytes)
        content, filename = p.read_bytes(), filename or p.name
    else:
        content, filename = path_or_bytes, filename or "upload.bin"
    return client.post(url, files={"file": (filename, content, content_type)}, data=data)
