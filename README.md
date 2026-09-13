# MarksWala

**AI-Assisted Evaluation. Teacher Approved.**

MarksWala helps university teachers evaluate handwritten answer sheets faster using AI, while keeping the teacher in full control of every mark.

![Bright Claymorphism UI](https://img.shields.io/badge/Design-Bright%20Claymorphism-C9A7FF?style=for-the-badge)
![Next.js 16](https://img.shields.io/badge/Frontend-Next.js%2016-000?style=for-the-badge&logo=nextdotjs)
![FastAPI](https://img.shields.io/badge/Backend-FastAPI-009688?style=for-the-badge&logo=fastapi)
![Gemini](https://img.shields.io/badge/AI-Google%20Gemini%20API-4285F4?style=for-the-badge&logo=googlegemini)

---

## How It Works

```
Course → Resources → Question Paper → Rubric → Answer Sheets → AI Evaluation → Teacher Review → Results
```

1. **Create a course** and upload reference materials (PDFs, PPTs, notes)
2. **Upload a question paper** — AI parses it into individual questions
3. **Review the auto-generated rubric** for each question, edit criteria, and approve
4. **Upload scanned answer sheets** — AI performs OCR, maps answers to questions, and evaluates against the rubric
5. **Review the AI evaluation** — accept, edit, or override scores with full transparency into AI reasoning and confidence
6. **Export results** as Excel

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Next.js 16, React 19, Tailwind CSS 4, shadcn/ui, Lucide Icons |
| Backend | FastAPI, SQLAlchemy, Pydantic |
| AI / LLM | Google Gemini API (cloud — no local model to run) |
| OCR | PyMuPDF |
| Database | SQLite (default) or MySQL |
| Vector DB | ChromaDB |
| Task Queue | Celery + Redis (optional, for background processing) |

---

## Prerequisites

Before you begin, make sure you have these installed:

- **Python 3.10+** — [python.org](https://www.python.org/downloads/)
- **Node.js 20+** — [nodejs.org](https://nodejs.org/)
- **A Google Gemini API key** — [aistudio.google.com/apikey](https://aistudio.google.com/apikey) (free tier available, no local model to run)

### Optional (for production / background processing)

- **Docker & Docker Compose** — for MySQL, Redis, Qdrant
- **Redis** — required if using Celery for async answer processing

---

## Quick Start

### 1. Clone the Repository

```bash
git clone <your-repo-url>
cd MarksWala
```

### 2. Set Up the Backend

```bash
cd backend

# Create a virtual environment
python -m venv venv

# Activate it
# Windows (PowerShell):
.\venv\Scripts\Activate.ps1
# Windows (CMD):
.\venv\Scripts\activate.bat
# macOS / Linux:
source venv/bin/activate

# Install dependencies (pinned, reproducible)
pip install -r requirements.txt
```

> **Note:** If `venv` was copied from another project and `pip` shows a "Fatal error in launcher" message, delete the `venv` folder and recreate it: `python -m venv venv`.

### 3. Configure your Gemini API key

Get a free API key at [aistudio.google.com/apikey](https://aistudio.google.com/apikey), then create `backend/.env` (copy from `backend/.env.example`):

```bash
cd backend
cp .env.example .env
# Edit .env and paste your key:
# GEMINI_API_KEY=your-key-here
```

> **Tip:** You can change the model via the `GEMINI_TEXT_MODEL` and `GEMINI_VISION_MODEL` environment variables (defaults to `gemini-2.5-flash`).

### 4. Start the Backend

```bash
cd backend

# Activate venv if not already active
# Windows: .\venv\Scripts\Activate.ps1
# macOS/Linux: source venv/bin/activate

uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

The API will be available at **http://localhost:8000**.  
Interactive docs at **http://localhost:8000/docs**.

> The backend uses SQLite by default — no database setup required. The file `markswala.db` is created automatically on first run.

### 5. Set Up the Frontend

Open a **new terminal**:

```bash
cd frontend

# Install dependencies
npm install

# The .env.local file is already configured:
# NEXT_PUBLIC_API_URL=http://localhost:8000/api/v1
```

### 6. Start the Frontend

```bash
cd frontend
npm run dev
```

The app will be available at **http://localhost:3000**.

### 7. Use MarksWala

1. Open **http://localhost:3000** in your browser
2. **Register** a new teacher account
3. **Log in** with your credentials
4. **Create a course**, upload materials, create an exam
5. **Upload a question paper** (PDF) — AI will parse questions
6. **Review and approve rubrics** for each question
7. **Upload scanned answer sheets** (PDF) — AI evaluates them
8. **Review AI evaluations** — accept, edit, or override scores

---

## Optional: Docker Services

If you want MySQL, Redis, and Qdrant instead of the default SQLite:

```bash
# From the project root
docker-compose up -d
```

This starts:
- **MySQL 8.0** on port `3306` (user: `root`, password: `root`, database: `markswala`)
- **Redis 7** on port `6379`
- **Qdrant** on port `6333`

Then set the `DATABASE_URI` environment variable before starting the backend:

```bash
# PowerShell
$env:DATABASE_URI = "mysql+pymysql://root:root@localhost:3306/markswala"

# Bash
export DATABASE_URI="mysql+pymysql://root:root@localhost:3306/markswala"
```

---

## Environment Variables

### Backend

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URI` | `sqlite:///./markswala.db` | Database connection string |
| `SECRET_KEY` | `super-secret-key-for-dev` | JWT signing key (change in production!) |
| `GEMINI_API_KEY` | _(none)_ | Google Gemini API key (required) — get one at [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| `GEMINI_TEXT_MODEL` | `gemini-2.5-flash` | Model for text evaluation |
| `GEMINI_VISION_MODEL` | `gemini-2.5-flash` | Model for OCR / vision tasks |
| `VECTOR_DB_PATH` | `./chroma_db` | ChromaDB storage path |

### Frontend

| Variable | Default | Description |
|----------|---------|-------------|
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000/api/v1` | Backend API base URL |

---

## Project Structure

```
MarksWala/
├── backend/
│   ├── main.py                  # FastAPI entry point
│   ├── app/
│   │   ├── api/                 # API routes
│   │   │   ├── api_v1.py        # Router aggregation
│   │   │   ├── deps.py          # Auth dependencies
│   │   │   └── endpoints/       # Route handlers
│   │   ├── core/
│   │   │   ├── config.py        # Settings & env vars
│   │   │   └── security.py      # JWT & password hashing
│   │   ├── db/                  # Database session & base
│   │   ├── models/              # SQLAlchemy models
│   │   ├── schemas/             # Pydantic schemas
│   │   ├── services/            # Business logic & AI
│   │   └── utils/               # Helpers
│   ├── uploads/                 # Uploaded files
│   └── chroma_db/               # Vector embeddings
│
├── frontend/
│   ├── src/
│   │   ├── app/                 # Next.js pages
│   │   │   ├── login/           # Auth pages
│   │   │   ├── register/
│   │   │   ├── dashboard/       # Main dashboard
│   │   │   ├── courses/[id]/    # Course management
│   │   │   └── exams/[id]/      # Exam, review, rubric
│   │   ├── components/
│   │   │   ├── ui/              # Clay design system
│   │   │   └── dashboard-layout.tsx
│   │   └── lib/                 # API client & utils
│   └── .env.local               # Frontend env
│
└── docker-compose.yml           # Optional infrastructure
```

---

## Design System

The UI uses a custom **Bright Claymorphism** design language:

- **Cool blue background** (`#DDEBFF`) with bright clay-colored cards
- **Tactile buttons** that lift on hover and press on click
- **Inset clay inputs** with lavender focus rings
- **AI content** in lavender, **teacher content** in mint — clear visual distinction
- **Confidence badges** with color + emoji + text for accessibility
- **Plus Jakarta Sans** typography

---

## License

This project is private.
