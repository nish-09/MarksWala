"""Seed a full demo workflow through the public web origin (Next.js proxy -> API -> worker).

    backend/.venv/Scripts/python scripts/seed_demo.py --base http://localhost:3000 --email you@x.edu --password ...
Requires the account to exist, the worker running and fixtures generated (scripts/generate_fixtures.py).
"""
import argparse
import sys
import time
from pathlib import Path

import httpx

FIX = Path(__file__).resolve().parents[1] / "fixtures"
ap = argparse.ArgumentParser()
ap.add_argument("--base", default="http://localhost:3000")
ap.add_argument("--email", required=True)
ap.add_argument("--password", required=True)
a = ap.parse_args()
c = httpx.Client(base_url=a.base, headers={"Origin": a.base}, timeout=120)


def ok(r):
    assert r.status_code < 300, f"{r.request.method} {r.request.url} -> {r.status_code} {r.text[:300]}"
    return r.json()


def wait(job_id, timeout=600):
    end = time.time() + timeout
    while time.time() < end:
        j = ok(c.get(f"/api/jobs/{job_id}"))
        if j["status"] in ("COMPLETED", "FAILED", "REQUIRES_REVIEW"):
            print("  job", j["kind"], j["status"], j.get("error_message") or "")
            assert j["status"] != "FAILED"
            return j
        time.sleep(1)
    raise TimeoutError(job_id)


ok(c.post("/api/auth/login", json={"email": a.email, "password": a.password}))
course = ok(c.post("/api/courses", json={"code": "CS201", "name": "Data Structures and Algorithms", "description": "Second-year core course"}))
for n in ["CS201_Syllabus.pdf", "CS201_Lecture_Notes.pdf", "CS201_Lecture_Slides.pptx"]:
    r = ok(c.post(f"/api/courses/{course['id']}/resources", files={"file": (n, (FIX / "course" / n).read_bytes())}))
    wait(r["job"]["id"])
exam = ok(c.post(f"/api/courses/{course['id']}/exams", json={"title": "Internal Assessment 1"}))
r = ok(c.post(f"/api/exams/{exam['id']}/question-paper", files={"file": ("paper.pdf", (FIX / "exam" / "CS201_Internal_Assessment_1.pdf").read_bytes())}))
wait(r["paper_job"]["id"])
ok(c.post(f"/api/exams/{exam['id']}/questions/confirm"))
wait(ok(c.post(f"/api/exams/{exam['id']}/rubric/generate", json={}))["id"])
ok(c.post(f"/api/exams/{exam['id']}/rubric/draft/approve"))
files = [("files", (f"student_{k}.pdf", (FIX / "answer_sheets" / f"student_{k}.pdf").read_bytes(), "application/pdf")) for k in "ABC"]
ok(c.post(f"/api/exams/{exam['id']}/answer-sheets", files=files))
end = time.time() + 900
while time.time() < end:
    rows = ok(c.get(f"/api/exams/{exam['id']}/answer-sheets"))
    if all(r["status"] in ("EVALUATED", "FAILED") for r in rows):
        break
    time.sleep(2)
print([(r["original_filename"], r["status"]) for r in rows])
print("exam", exam["id"])
