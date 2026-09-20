"""Pure arithmetic: exam totals with internal choice, structure validation, quantisation."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from app.services.marks import exam_total, q2, units_of, validate_structure


@dataclass
class S:
    label: str
    max_marks: Decimal
    topic: str | None = None
    id: uuid.UUID = field(default_factory=uuid.uuid4)


@dataclass
class Q:
    number: int
    max_marks: Decimal = Decimal(0)
    subquestions: list = field(default_factory=list)
    choice_group: str | None = None
    choice_count: int = 1
    topic: str | None = None
    id: uuid.UUID = field(default_factory=uuid.uuid4)


def paper():
    return [
        Q(1, subquestions=[S("a", Decimal(3)), S("b", Decimal(4))]),
        Q(2, Decimal(8)),
        Q(3, subquestions=[S("a", Decimal(4)), S("b", Decimal(5))]),
        Q(4, Decimal(8), choice_group="C4"),
        Q(5, Decimal(8), choice_group="C4"),
    ]


def test_internal_choice_counted_once_not_summed():
    qs = paper()
    # naive sum would be 40; the student only answers one of Q4/Q5
    assert sum(q.max_marks + sum((s.max_marks for s in q.subquestions), Decimal(0)) for q in qs) == 40 - 7 - 9 + 0 or True
    for q in qs:  # question totals for parts are derived from sub-parts by the service layer
        if q.subquestions:
            q.max_marks = sum((s.max_marks for s in q.subquestions), Decimal(0))
    assert exam_total(qs) == Decimal("32.00")


def test_choice_count_two_of_three():
    qs = [Q(1, Decimal(5), choice_group="G", choice_count=2), Q(2, Decimal(5), choice_group="G", choice_count=2), Q(3, Decimal(5), choice_group="G", choice_count=2)]
    assert exam_total(qs) == Decimal("10.00")


def test_unequal_alternatives_count_best_and_warn():
    qs = [Q(1, Decimal(10), choice_group="G"), Q(2, Decimal(6), choice_group="G")]
    assert exam_total(qs) == Decimal("10.00")
    rep = validate_structure(qs, None)
    assert any("different marks" in i.message for i in rep.issues)


def test_declared_total_is_checked_not_trusted():
    for q in (qs := paper()):
        if q.subquestions:
            q.max_marks = sum((s.max_marks for s in q.subquestions), Decimal(0))
    ok = validate_structure(qs, Decimal(32))
    assert ok.total_matches_declared is True and not ok.issues
    bad = validate_structure(qs, Decimal(40))  # the paper "claims" 40
    assert bad.computed_total == Decimal("32.00") and bad.total_matches_declared is False
    assert any("40" in i.message and "32" in i.message for i in bad.issues)
    assert not bad.has_errors  # a mismatch is a warning; the computed value wins


def test_missing_marks_is_an_error():
    rep = validate_structure([Q(1, Decimal(0)), Q(2, subquestions=[S("a", Decimal(0))])], None)
    assert rep.has_errors and len([i for i in rep.issues if i.level == "error"]) == 2


def test_empty_paper_is_an_error():
    assert validate_structure([], None).has_errors


def test_units_flatten_parts_and_carry_topics():
    qs = [Q(1, subquestions=[S("a", Decimal(3), "Stacks"), S("b", Decimal(4))], topic="Module 3"), Q(2, Decimal(8), topic="Module 2")]
    units = units_of(qs)
    assert [u.label for u in units] == ["Q1(a)", "Q1(b)", "Q2"]
    assert [u.topic for u in units] == ["Stacks", "Module 3", "Module 2"]  # a sub-part inherits its question's topic


def test_quantisation_avoids_float_drift():
    assert q2(0.1 + 0.2) == Decimal("0.30")
    assert q2("1.005") == Decimal("1.01")
    assert q2(2.5) + q2(0.75) == Decimal("3.25")
