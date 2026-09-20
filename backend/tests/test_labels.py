"""Question-label parsing: every style a student might write."""
from __future__ import annotations

import pytest

from app.services.labels import ParsedLabel, detect_start, parse_label, resolve_unit


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Q1", (1, None)), ("Q1(a)", (1, "a")), ("Q1 b", (1, "b")), ("1.a", (1, "a")), ("1(b)", (1, "b")),
        ("Q2", (2, None)), ("Q2(a)", (2, "a")), ("Ans 1(a).", (1, "a")), ("Ans 3.", (3, None)), ("Answer 2", (2, None)),
        ("Q.1 a)", (1, "a")), ("Q.2", (2, None)), ("Question 3 (b)", (3, "b")), ("3-b", (3, "b")), ("q4", (4, None)),
        ("  Q10(c)  ", (10, "c")), ("Q3 (ii)", (3, "ii")), ("Q.4 iii)", (4, "iii")), ("QUESTION 5", (5, None)),
        ("Ans. 2(b)", (2, "b")), ("No. 1", None), ("Hello", None), ("", None), ("Q", None), ("(a)", None),
    ],
)
def test_parse_label_styles(raw, expected):
    got = parse_label(raw)
    assert (got and (got.number, got.sub)) == expected


@pytest.mark.parametrize(
    "line,expected",
    [
        ("Q1(a) A stack is a linear data structure", (1, "a")),
        ("Ans 1(b). Stack follows LIFO", (1, "b")),
        ("Q.2 Binary search is used", (2, None)),
        ("Q.1 a) A stack works", (1, "a")),
        ("Ans 3.", (3, None)),
        ("2(b) Compare BFS and DFS", (2, "b")),
        ("1.a A stack is", (1, "a")),
        ("Q2", (2, None)),
        ("Question 4: reversing a list", (4, None)),
        # must NOT be treated as labels
        ("1 stack is used for undo", None),
        ("The complexity is O(log n)", None),
        ("Applications: 1) undo operation 2) function calls", None),
        ("2 steps are needed", None),
        ("Name: Aarav Sharma", None),
        ("Roll No: CS2024-001", None),
        ("Ans is 42", None),
    ],
)
def test_detect_start_of_line(line, expected):
    got = detect_start(line)
    assert (got and (got.number, got.sub)) == expected


SUBS = {1: ["a", "b"], 2: [], 3: ["a", "b"], 4: [], 5: []}


@pytest.mark.parametrize(
    "label,expected",
    [
        (ParsedLabel(1, "a"), (1, "a")),
        (ParsedLabel(2, None), (2, None)),
        (ParsedLabel(3, "b"), (3, "b")),
        (ParsedLabel(1, None), None),   # Q1 has parts but no part given -> ambiguous
        (ParsedLabel(2, "a"), None),    # Q2 has no parts
        (ParsedLabel(1, "c"), None),    # no such part
        (ParsedLabel(9, None), None),   # no such question
    ],
)
def test_resolve_unit(label, expected):
    assert resolve_unit(label, SUBS) == expected
