"""Question paper ingestion: text (or OCR) -> LLM structure extraction -> programmatic validation -> questions."""
from __future__ import annotations

import logging
import re

import pymupdf as fitz
from pydantic import BaseModel, Field
from sqlalchemy import select, update

from app.db.session import session_scope
from app.models import Exam, ResourceChunk
from app.models.enums import JobKind, PaperStatus
from app.providers import registry
from app.providers.storage import get_storage
from app.schemas.exams import QuestionIn, SubquestionIn
from app.services import documents
from app.services.jobs import JobContext, JobError, JobResult, describe_error, handler
from app.services.marks import q2
from app.services.structure import apply_structure, norm_label

log = logging.getLogger("MarksWala.paper")
PROMPT_VERSION = "paper-parse-v1"


class ParsedSub(BaseModel):
    label: str = Field(description="Sub-part label such as 'a', 'b', 'i'")
    text: str
    max_marks: float | None = Field(default=None, description="Marks printed for this sub-part, or null if not printed")


class ParsedQuestion(BaseModel):
    number: int = Field(description="The question number, e.g. 3 for 'Q3'")
    section: str | None = Field(default=None, description="Section heading the question sits under, if any")
    text: str = Field(description="The full question text (stem). Empty string if the question consists only of sub-parts.")
    max_marks: float | None = Field(default=None, description="Marks printed for the whole question when it has no sub-parts; null otherwise")
    topic: str | None = Field(default=None, description="Best-matching course topic/module from the provided list, else null")
    choice_with: int | None = Field(
        default=None,
        description="If this question is an internal-choice alternative (an 'OR' option) of an EARLIER question, the number of that question; else null",
    )
    subquestions: list[ParsedSub] = Field(default_factory=list)


class ParsedPaper(BaseModel):
    declared_total_marks: float | None = Field(default=None, description="The total/maximum marks printed on the paper header, or null")
    questions: list[ParsedQuestion]


SYSTEM = (
    "You extract the exact structure of a university exam question paper. Be faithful: copy question text and marks as "
    "printed; never invent questions, sub-parts or marks. Marks that are not printed must be null (never guess). "
    "Sub-part labels are normalised to lower-case letters or roman numerals without brackets (a, b, c / i, ii). "
    "A question whose parts are listed as (a), (b)... has those parts as subquestions and the parent's own marks are null. "
    "Recognise internal choice: when two questions are joined by 'OR' or 'attempt any one', the later question is an "
    "alternative of the earlier one (choice_with = earlier question number)."
)


def _paper_text(data: bytes, kind: str, mime: str, ctx: JobContext) -> str:
    if kind == "pdf":
        doc = pymupdf_open(data)
        try:
            pages = []
            for i, page in enumerate(doc, start=1):
                txt = page.get_text("text").strip()
                if len(txt) < 40 and page.get_images():
                    ctx.progress(None, f"Reading scanned page {i} with OCR")
                    txt = registry.get_ocr().transcribe_page(page.get_pixmap(matrix=fitz.Matrix(2, 2)).tobytes("png"), page_number=i).text
                pages.append(txt)
            return "\n\n".join(pages)
        finally:
            doc.close()
    if kind == "docx":
        return "\n\n".join(u.text for u in documents.extract_docx(data))
    return registry.get_ocr().transcribe_page(data, page_number=1, mime_type=mime).text


def pymupdf_open(data: bytes):
    return fitz.open(stream=data, filetype="pdf")


def _to_inputs(parsed: ParsedPaper) -> list[QuestionIn]:
    """Normalise LLM output into validated QuestionIn objects; resolve internal-choice alternatives into groups."""
    seen: set[int] = set()
    for q in parsed.questions:
        if q.number in seen:
            raise JobError(f"The paper parser found question number {q.number} twice. Please edit the questions manually.", code="ambiguous_numbering")
        seen.add(q.number)

    # union-find over choice_with
    parent = {q.number: q.number for q in parsed.questions}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for q in parsed.questions:
        if q.choice_with is not None and q.choice_with in parent and q.choice_with != q.number:
            parent[find(q.number)] = find(q.choice_with)
    members: dict[int, list[int]] = {}
    for n in parent:
        members.setdefault(find(n), []).append(n)

    out: list[QuestionIn] = []
    for q in parsed.questions:
        root_members = sorted(members[find(q.number)])
        group = f"C{root_members[0]}" if len(root_members) > 1 else None
        subs = [
            SubquestionIn(label=norm_label(s.label) or str(i + 1), text=s.text.strip() or "(text not captured)", max_marks=float(s.max_marks or 0))
            for i, s in enumerate(q.subquestions)
        ]
        out.append(
            QuestionIn(
                number=q.number,
                section=q.section,
                text=q.text or "",
                max_marks=0 if subs else float(q.max_marks or 0),
                topic=q.topic,
                choice_group=group,
                choice_count=1,
                subquestions=subs,
            )
        )
        if not subs and not out[-1].text.strip():
            out[-1].text = "(text not captured)"
    return out


@handler(JobKind.PARSE_QUESTION_PAPER)
def parse_question_paper(ctx: JobContext) -> JobResult:
    exam_id = ctx.entity_id
    with session_scope() as db:
        exam = db.get(Exam, exam_id)
        if exam is None or exam.deleted_at is not None or not exam.paper_storage_key:
            raise JobError("The exam or its question paper no longer exists.", code="paper_missing")
        key, course_id = exam.paper_storage_key, exam.course_id
        exam.paper_status, exam.paper_error = PaperStatus.PROCESSING, None
        topics = [
            r[0]
            for r in db.execute(
                select(ResourceChunk.section)
                .where(ResourceChunk.course_id == course_id, ResourceChunk.section.ilike("module%"))
                .distinct()
                .limit(40)
            )
        ]
    try:
        data = get_storage().read_bytes(key)
        kind = "pdf" if data.startswith(b"%PDF-") else "docx" if data.startswith(b"PK") else "png" if data.startswith(b"\x89PNG") else "jpeg"
        ctx.progress(0.1, "Reading the question paper")
        text = _paper_text(data, kind, "image/png" if kind == "png" else "image/jpeg", ctx).strip()
        if len(re.sub(r"\s+", "", text)) < 20:
            raise JobError("No readable text was found in the question paper.", code="no_text")

        ctx.progress(0.4, "Extracting questions, sub-parts and marks")
        topic_hint = ("Course topics to choose from for `topic` (use the exact string or null):\n- " + "\n- ".join(topics) + "\n\n") if topics else ""
        parsed = registry.get_ai().generate_structured(
            system=SYSTEM,
            prompt=f"{topic_hint}QUESTION PAPER TEXT:\n\"\"\"\n{text[:30000]}\n\"\"\"",
            schema=ParsedPaper,
        )
        if not parsed.questions:
            raise JobError("No questions could be identified in this paper. Check the file, or enter the questions manually.", code="no_questions")

        ctx.progress(0.8, "Validating marks")
        inputs = _to_inputs(parsed)
        with session_scope() as db:
            exam = db.get(Exam, exam_id)
            apply_structure(db, exam, inputs)
            exam.declared_total_marks = q2(parsed.declared_total_marks) if parsed.declared_total_marks else None
            exam.paper_status = PaperStatus.PARSED
            exam.paper_error = None
        return JobResult(message=f"{len(inputs)} questions parsed")
    except BaseException as e:
        with session_scope() as db:
            db.execute(update(Exam).where(Exam.id == exam_id).values(paper_status=PaperStatus.FAILED, paper_error=describe_error(e)[:500]))
        raise
