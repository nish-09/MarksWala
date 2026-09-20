"""Confidence signals and review flags. Pure functions; thresholds are passed in (configurable per exam).

These are heuristic *signals* to direct the teacher's attention, not statistical certainty.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.models.enums import FlagKind, FlagSeverity

WEIGHTS = {"ocr": 0.30, "mapping": 0.25, "retrieval": 0.10, "rubric": 0.10, "evaluation": 0.25}
# components whose failure means the marks themselves may be wrong -> mandatory review when very low
CRITICAL = {"ocr", "mapping", "evaluation"}
KIND = {"ocr": FlagKind.LOW_OCR, "mapping": FlagKind.LOW_MAPPING, "retrieval": FlagKind.LOW_RETRIEVAL,
        "rubric": FlagKind.LOW_RUBRIC, "evaluation": FlagKind.LOW_EVALUATION}
LABEL = {"ocr": "Handwriting/OCR", "mapping": "Question mapping", "retrieval": "Course-material match",
         "rubric": "Rubric grounding", "evaluation": "AI evaluation"}


def overall_confidence(components: dict[str, float | None]) -> float:
    """Weighted mean of the available components, capped so that one very weak component cannot be averaged away."""
    present = {k: v for k, v in components.items() if v is not None}
    if not present:
        return 0.0
    wsum = sum(WEIGHTS[k] for k in present)
    mean = sum(WEIGHTS[k] * v for k, v in present.items()) / wsum
    return round(max(0.0, min(1.0, min(mean, min(present.values()) + 0.20))), 3)


@dataclass
class FlagSpec:
    kind: FlagKind
    severity: FlagSeverity
    detail: str
    value: float | None = None
    threshold: float | None = None


def band(value: float, high: float, review: float) -> str:
    return "high" if value >= high else "review_recommended" if value >= review else "mandatory_review"


def build_flags(
    components: dict[str, float | None],
    overall: float,
    *,
    high: float,
    review: float,
    missing_unexpected: bool = False,
    unreadable: list[str] | None = None,
    has_diagram: bool = False,
) -> list[FlagSpec]:
    flags: list[FlagSpec] = []
    for name, v in components.items():
        if v is not None and v < review:
            sev = FlagSeverity.MANDATORY if name in CRITICAL else FlagSeverity.RECOMMENDED
            flags.append(FlagSpec(KIND[name], sev, f"{LABEL[name]} confidence is {v:.0%}, below the {review:.0%} review threshold.", v, review))
    if overall < review:
        flags.append(FlagSpec(FlagKind.LOW_OVERALL, FlagSeverity.MANDATORY, f"Overall confidence is {overall:.0%} (below {review:.0%}): teacher review required.", overall, review))
    elif overall < high:
        flags.append(FlagSpec(FlagKind.LOW_OVERALL, FlagSeverity.RECOMMENDED, f"Overall confidence is {overall:.0%} (below {high:.0%}): review recommended.", overall, high))
    if missing_unexpected:
        flags.append(FlagSpec(FlagKind.MISSING_ANSWER, FlagSeverity.RECOMMENDED, "No answer was found for this question. Confirm the student left it blank and it was not missed or misplaced."))
    if unreadable:
        flags.append(FlagSpec(FlagKind.UNREADABLE, FlagSeverity.RECOMMENDED, "Hard-to-read handwriting: " + "; ".join(unreadable[:3])))
    if has_diagram:
        flags.append(FlagSpec(FlagKind.LOW_OCR, FlagSeverity.RECOMMENDED, "The answer includes a diagram. Compare the drawing on the page image with the marks given."))
    return flags
