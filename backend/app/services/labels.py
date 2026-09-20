"""Parsing of question labels as students write them: Q1, Q1(a), Q1 b, 1.a, 1(b), Ans 2, Question 3 (b), Q.4 ii)...

Pure functions (no I/O) so the behaviour can be tested exhaustively.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_PREFIX = r"(?:(?:questions?|ques?|qn|q|answers?|ans)\s*[.:\-]?\s*(?:no\.?)?\s*)"
_ROMAN = r"(?:x{0,1}(?:ix|iv|v?i{0,3}))"
_SUB = rf"(?:[a-z]|{_ROMAN})"

# a *whole-string* label, e.g. "Q1(a)", "Ans 2.", "1.a", "Q.4 ii)"
_FULL = re.compile(
    rf"^\s*{_PREFIX}?(?P<num>\d{{1,3}})\s*(?:[.\-:]?\s*[\(\[]?\s*(?P<sub>{_SUB})\s*[\)\]]?)?\s*[.:)\-]*\s*$",
    re.IGNORECASE,
)
# a label at the START of a line followed by answer text, e.g. "Ans 1(a). A stack is ..." or "Q2 Binary search ..."
_START = re.compile(
    rf"^\s*{_PREFIX}(?P<num>\d{{1,3}})\s*(?:[.\-:]?\s*[\(\[]\s*(?P<sub>{_SUB})\s*[\)\]]|[.\-:]?\s+(?P<sub2>[a-z])\s*\))?\s*(?P<sep>[.:)\-]|\s|$)",
    re.IGNORECASE,
)
_START_BARE = re.compile(rf"^\s*(?P<num>\d{{1,3}})\s*[.\-:]?\s*[\(\[]\s*(?P<sub>{_SUB})\s*[\)\]]\s*(?P<sep>[.:)\-]|\s|$)", re.IGNORECASE)
_START_DOTSUB = re.compile(rf"^\s*(?P<num>\d{{1,3}})\s*[.\-]\s*(?P<sub>[a-z])\s*(?P<sep>[.:)\-]|\s)", re.IGNORECASE)


@dataclass(frozen=True)
class ParsedLabel:
    number: int
    sub: str | None  # normalised lower-case: "a", "ii"

    def __str__(self) -> str:
        return f"Q{self.number}" + (f"({self.sub})" if self.sub else "")


def _norm_sub(sub: str | None) -> str | None:
    return sub.lower() if sub else None


def parse_label(raw: str | None) -> ParsedLabel | None:
    """Parse a label that is (only) a question reference. Returns None when it is not one."""
    if not raw:
        return None
    m = _FULL.match(raw.strip())
    if not m:
        return None
    return ParsedLabel(int(m.group("num")), _norm_sub(m.group("sub")))


def match_start(line: str) -> tuple[ParsedLabel, int] | None:
    """Detect a question label at the beginning of an OCR line. Returns (label, index where the label ends)."""
    s = line.strip()
    if not s or len(s) > 400:
        return None
    offset = len(line) - len(line.lstrip())
    for rx in (_START, _START_BARE, _START_DOTSUB):
        m = rx.match(s)
        if m:
            sub = m.groupdict().get("sub") or m.groupdict().get("sub2")
            # a lone "1 " or "2 " at the start of a sentence is too weak to be a label unless prefixed/punctuated
            if rx is _START and not re.match(r"^\s*(?:questions?|ques?|qn|q|answers?|ans)(?![a-z])", s, re.IGNORECASE) and not sub:
                continue
            end = m.end("sep") if m.group("sep") in {".", ":", ")", "-"} else m.start("sep")
            return ParsedLabel(int(m.group("num")), _norm_sub(sub)), offset + end
    return None


def detect_start(line: str) -> ParsedLabel | None:
    hit = match_start(line)
    return hit[0] if hit else None


def strip_label(line: str) -> str:
    """The line without a leading question label (unchanged if it has none)."""
    hit = match_start(line)
    return line[hit[1]:].lstrip(" .:-	") if hit else line


def resolve_unit(label: ParsedLabel, questions_subs: dict[int, list[str]]) -> tuple[int, str | None] | None:
    """Map a parsed label onto an existing gradable unit.

    `questions_subs` maps question number -> its sub-part labels ([] when the question has none).
    Returns (question_number, sub_label|None) or None when the label cannot be resolved unambiguously.
    """
    subs = questions_subs.get(label.number)
    if subs is None:
        return None
    if not subs:
        return (label.number, None) if label.sub is None else None
    if label.sub is None:
        return None  # "Q3" written but Q3 has parts (a)/(b): ambiguous, needs the part label
    return (label.number, label.sub) if label.sub in subs else None
