"""Pure mark arithmetic: gradable units, exam totals (with internal choice) and structure validation.

Nothing here touches the database or an LLM, so totals are always derived from persisted question data.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable, Protocol

TWO = Decimal("0.01")


def q2(v: Decimal | float | int | str) -> Decimal:
    """Quantize to two decimals (marks are stored as NUMERIC(8,2))."""
    return Decimal(str(v)).quantize(TWO, rounding=ROUND_HALF_UP)


class SubLike(Protocol):
    id: uuid.UUID
    label: str
    max_marks: Decimal
    topic: str | None


class QuestionLike(Protocol):
    id: uuid.UUID
    number: int
    max_marks: Decimal
    topic: str | None
    choice_group: str | None
    choice_count: int
    subquestions: list


@dataclass(frozen=True)
class Unit:
    """A gradable unit: a question without parts, or one part of a question."""

    question_id: uuid.UUID
    subquestion_id: uuid.UUID | None
    number: int
    label: str  # "Q1" or "Q1(a)"
    max_marks: Decimal
    topic: str | None


def units_of(questions: Iterable[QuestionLike]) -> list[Unit]:
    out: list[Unit] = []
    for q in sorted(questions, key=lambda x: x.number):
        if q.subquestions:
            for s in q.subquestions:
                out.append(Unit(q.id, s.id, q.number, f"Q{q.number}({s.label})", s.max_marks, s.topic or q.topic))
        else:
            out.append(Unit(q.id, None, q.number, f"Q{q.number}", q.max_marks, q.topic))
    return out


def question_marks(q: QuestionLike) -> Decimal:
    return q2(sum((s.max_marks for s in q.subquestions), Decimal(0))) if q.subquestions else q2(q.max_marks)


def exam_total(questions: Iterable[QuestionLike]) -> Decimal:
    """Maximum obtainable marks. Standalone questions count in full; in an internal-choice group the
    student answers `choice_count` of the alternatives, so only the best-paying `choice_count` count."""
    qs = list(questions)
    total = Decimal(0)
    groups: dict[str, list[QuestionLike]] = {}
    for q in qs:
        if q.choice_group:
            groups.setdefault(q.choice_group, []).append(q)
        else:
            total += question_marks(q)
    for members in groups.values():
        n = max(m.choice_count for m in members)
        best = sorted((question_marks(m) for m in members), reverse=True)[:n]
        total += sum(best, Decimal(0))
    return q2(total)


@dataclass
class Issue:
    level: str  # "error" blocks confirmation; "warning" is informational
    message: str
    question_number: int | None = None

    def as_dict(self) -> dict:
        return {"level": self.level, "message": self.message, "question_number": self.question_number}


@dataclass
class StructureReport:
    computed_total: Decimal
    declared_total: Decimal | None
    issues: list[Issue] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(i.level == "error" for i in self.issues)

    @property
    def total_matches_declared(self) -> bool | None:
        return None if self.declared_total is None else q2(self.declared_total) == self.computed_total


def validate_structure(questions: Iterable[QuestionLike], declared_total: Decimal | None) -> StructureReport:
    qs = sorted(questions, key=lambda q: q.number)
    issues: list[Issue] = []
    if not qs:
        issues.append(Issue("error", "No questions have been parsed yet."))
    for q in qs:
        if q.subquestions:
            for s in q.subquestions:
                if s.max_marks <= 0:
                    issues.append(Issue("error", f"Q{q.number}({s.label}) has no marks. Enter the maximum marks.", q.number))
        elif q.max_marks <= 0:
            issues.append(Issue("error", f"Q{q.number} has no marks. Enter the maximum marks.", q.number))
    groups: dict[str, list[QuestionLike]] = {}
    for q in qs:
        if q.choice_group:
            groups.setdefault(q.choice_group, []).append(q)
    for g, members in groups.items():
        nums = ", ".join(f"Q{m.number}" for m in members)
        if len(members) < 2:
            issues.append(Issue("warning", f"Question {nums} is marked as an internal choice but has no alternative.", members[0].number))
        if len({question_marks(m) for m in members}) > 1:
            issues.append(Issue("warning", f"Alternatives {nums} carry different marks; the highest-scoring option is counted in the exam total.", members[0].number))
    computed = exam_total(qs)
    if declared_total is not None and q2(declared_total) != computed:
        issues.append(
            Issue(
                "warning",
                f"The paper states {q2(declared_total)} total marks but the questions add up to {computed}. "
                "Check for a missed question, an internal choice, or a mistyped mark.",
            )
        )
    return StructureReport(computed_total=computed, declared_total=declared_total, issues=issues)
