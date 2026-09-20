# MarksWala

**AI-assisted evaluation of handwritten university answer sheets. The teacher always has the final say.**

A teacher creates a course, uploads its material, uploads a question paper, approves an AI-drafted rubric, uploads scanned
handwritten answer sheets, reviews what the AI is unsure about, and exports final marks. Every mark is traceable from the
final number back to the handwritten page:

```
Exam → Student → Answer sheet → Page → Question → Original OCR → Corrected OCR → Rubric version → Criterion
     → Retrieved course sources → AI evaluation → Confidence → Teacher review → Teacher override → Final mark
```

## Architecture

| Layer | Technology |
|---|---|
| Frontend | Next.js (App Router), React, TypeScript, Tailwind, TanStack Query, Zod — Claymorphism UI |
| API | FastAPI, SQLAlchemy 2, Alembic, Pydantic |
| Database | PostgreSQL (migrations only; triggers guard immutability) |
| Queue | Redis + Dramatiq worker. Job state lives in PostgreSQL (`processing_jobs`), Redis only carries messages |
| Vector store | Qdrant (every query is scoped to a course) |
| AI | Provider interfaces `AIProvider`, `OCRProvider`, `EmbeddingProvider`, `VectorStore`, `StorageProvider`; Gemini is the V1 implementation |

Key design rules (each is enforced in code and covered by tests):

* **The AI never sets a mark.** It judges each rubric criterion; the backend validates the structure, rejects malformed or
  inconsistent output, enforces `0 ≤ criterion ≤ max` and computes every total. Empty answers get a deterministic zero (no model call).
* **Nothing is overwritten.** Original OCR, AI evaluations, teacher overrides and the audit log are protected by database triggers
  (`backend/alembic/versions/*_integrity_triggers.py`). Corrections and overrides are stored beside the originals.
* **Approved rubric versions are frozen** (trigger) and every evaluation records the version it used.
* **Results, analytics, feedback and the Excel export are computed from persisted records**, never in the browser or by the LLM.
* **Confidence is a signal, not certainty.** OCR / mapping / retrieval / rubric / evaluation confidence are stored separately;
  thresholds (default 85 / 70) are configurable per exam.
* **Authorization is server-side.** Every object is reached through its course; non-members get `404`, never `403` or data.

## Quick start (Docker)

```bash
cp .env.example .env            # set GEMINI_API_KEY and a real SECRET_KEY
docker compose up --build       # → http://localhost:3000
```

Production: `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build` behind a TLS reverse proxy
(requires `SECRET_KEY` ≥ 32 chars, `COOKIE_SECURE=true`; the API refuses to start otherwise).

## Local development without Docker

Requirements: Python 3.11+, Node 20+, PostgreSQL 16, Redis, Qdrant.

```bash
# backend
cd backend && python -m venv .venv && .venv/Scripts/pip install -r requirements.txt     # (bin/ on macOS/Linux)
cp ../.env.example ../.env                       # then edit
.venv/Scripts/alembic upgrade head               # fresh database → complete schema
.venv/Scripts/uvicorn app.main:app --port 8000
.venv/Scripts/dramatiq app.tasks.actors --processes 1 --threads 4    # separate terminal: the worker

# frontend
cd frontend && npm install && npm run dev        # → http://localhost:3000  (proxies /api/* to BACKEND_URL)
```

> On Windows use `127.0.0.1`, not `localhost`, in `DATABASE_URL` / `REDIS_URL` / `QDRANT_URL`: `localhost` tries IPv6 first and
> added seconds to every call in our measurements.

## Environment variables

See `.env.example` (documented inline). Required: `DATABASE_URL`, `REDIS_URL`, `QDRANT_URL`, `GEMINI_API_KEY`, `SECRET_KEY`.
Optional: `GEMINI_TEXT_MODEL`, `GEMINI_VISION_MODEL`, `GEMINI_EVAL_MODEL`, storage limits, session/cookie/CORS settings,
`CONFIDENCE_*_THRESHOLD`, `DEFAULT_PASS_PERCENTAGE`. `AI_RESPONSE_CACHE_DIR` is a **dev/test-only** replay cache; leave it unset in production.
The AI key is only ever read by the backend and worker — never by the browser.

## Database

Alembic is the only schema mechanism (`create_all()` is never used). A fresh database needs exactly `alembic upgrade head`.
`alembic check` reports no drift between models and migrations.

## Tests

```bash
cd backend
pytest -m "not live_ai"                       # unit + integration + failure injection + security, real Postgres/Redis/Qdrant
pytest tests/test_e2e_acceptance.py -s        # full workflow with a real worker and real Gemini (uses recorded responses if .ai-cache exists)
MARKSWALA_NO_AI_CACHE=1 pytest tests/test_e2e_acceptance.py -s   # force live API calls
python ../scripts/generate_fixtures.py        # regenerate the DSA course, paper and 3 handwriting-style answer sheets
```

Test data is realistic: a CS201 course (syllabus, lecture notes, slides), a 32-mark paper with sub-parts and an internal choice
(Q4 OR Q5 — the naive sum is 40, the correct total 32), and three handwritten-style sheets (strong / partial / weak) using different
question-label styles and an out-of-order answer. Handwriting is synthesized from open handwriting fonts — a realistic stimulus,
not real student handwriting.

## Repository layout

```
backend/    app/{api,core,db,models,providers,schemas,services,tasks}  alembic/  tests/
frontend/   src/{app,components,lib}   (API types generated from the backend OpenAPI schema)
scripts/    generate_fixtures.py  export_openapi.py  seed_demo.py
fixtures/   generated test course material, paper and answer sheets
```

## Known limitations

See the final report delivered with this rebuild; in short: Docker files are unvalidated on the build machine, Excel/Results/Analytics
issue ~13 queries per student (fine for classes of tens, not thousands), legacy `.ppt`/`.doc` are rejected, and the free Gemini tier's
daily quota is very small.
