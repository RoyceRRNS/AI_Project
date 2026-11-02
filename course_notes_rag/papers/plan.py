"""The revision plan: which sections to revise first, and how to practise each one.

Everything in it is computed from the blueprint and the student's mastery scores, so each line
can be traced back to real past questions. No text here comes from the language model.
"""

from __future__ import annotations

import math
from typing import Any

from course_notes_rag.papers.taxonomy import PRACTICE_LABELS, PRACTICE_MODES

MUST_REVISE_SHARE = 0.6
SHOULD_REVISE_SHARE = 0.85
DEFAULT_MASTERY = 0.5
LOW_MASTERY = 0.4
MAX_DAYS = 60

TIERS = {"must": "Must revise", "should": "Should revise", "optional": "If time permits"}

PRACTICE_ADVICE = {
    "numericals": "Work the formulas from the notes on fresh numbers until you can do them unaided.",
    "derivations": "Write the derivation end to end from a blank page, then check it against the notes.",
    "algorithm_tracing": "Trace the procedure by hand on a small input, showing the state after every step.",
    "diagrams": "Draw and label the diagram from memory, then explain each part.",
    "case_studies": "Practise turning a scenario into the right method and justifying the choice.",
    "theory": "Learn the definitions and write short, structured answers.",
}


def practice_breakdown(types: dict[str, float]) -> list[dict[str, Any]]:
    """Marks by what the student has to do, largest first."""
    modes: dict[str, float] = {}
    for question_type, marks in types.items():
        mode = PRACTICE_MODES.get(question_type)
        if mode:
            modes[mode] = modes.get(mode, 0.0) + marks
    total = sum(modes.values())
    rows = [
        {"mode": mode, "label": PRACTICE_LABELS[mode],
         "share": round(marks / total, 4) if total else 0.0}
        for mode, marks in modes.items()
    ]
    return sorted(rows, key=lambda r: -r["share"])


def _advice(section: dict[str, Any], paper_count: int, focus: str, score: float, attempts: int) -> str:
    text = (
        f"Asked in {section['papers']} of {paper_count} paper{'s' if paper_count != 1 else ''}, "
        f"about {section['marks_per_paper']:g} marks a paper. {PRACTICE_ADVICE[focus]}"
    )
    if attempts and score < LOW_MASTERY:
        text += " Your quiz results here are low, so start with this one."
    return text


def _schedule(items: list[dict[str, Any]], days: int) -> list[dict[str, Any]]:
    """Share sections across the days by weight; the last day is kept for a timed mock paper."""
    study_days = max(1, days - 1) if days >= 2 else 1
    per_day = math.ceil(len(items) / study_days) if items else 0
    schedule = []
    for day in range(study_days):
        chunk = items[day * per_day : (day + 1) * per_day]
        if chunk:
            schedule.append({
                "day": day + 1, "title": f"Day {day + 1}",
                "sections": [s["section_title"] for s in chunk],
                "focus": list(dict.fromkeys(s["focus_label"] for s in chunk)),
                "expected_marks": round(sum(s["expected_marks"] for s in chunk), 2),
            })
    if days >= 2:
        schedule.append({
            "day": len(schedule) + 1, "title": "Timed mock paper", "sections": [],
            "focus": ["Sit a mock paper under exam conditions, then review it"],
            "expected_marks": 0.0,
        })
    return schedule


def build_plan(
    blueprint: dict[str, Any], mastery: dict[str, dict[str, Any]], *, days: int | None = None
) -> dict[str, Any]:
    if days is not None and not 1 <= days <= MAX_DAYS:
        raise ValueError(f"days must be between 1 and {MAX_DAYS}")
    paper_count = blueprint["summary"]["papers"]
    items: list[dict[str, Any]] = []
    for section in blueprint["sections"]:
        known = mastery.get(section.get("parent_id") or "", {})
        score = float(known.get("score", DEFAULT_MASTERY))
        attempts = int(known.get("attempts", 0))
        raw = section["expected_marks"] * (0.5 + 0.5 * section["frequency"]) * (1.5 - score)
        practice = practice_breakdown(section["types"])
        focus = practice[0]["mode"] if practice else "theory"
        items.append({
            **{k: section.get(k) for k in (
                "parent_id", "section_title", "module", "notes_pdf", "document_id", "page_start",
                "page_end", "marks_per_paper", "expected_marks", "frequency", "papers", "years",
                "examples",
            )},
            "raw": raw, "mastery": score, "mastery_attempts": attempts, "focus": focus,
            "focus_label": PRACTICE_LABELS[focus], "practice": practice,
            "advice": _advice(section, paper_count, focus, score, attempts),
        })
    items.sort(key=lambda i: -i["raw"])
    top = items[0]["raw"] if items else 1.0
    total = sum(i["expected_marks"] for i in items) or 1.0
    running = 0.0
    for rank, item in enumerate(items, start=1):
        item["rank"] = rank
        item["priority"] = round(100 * item.pop("raw") / top, 1) if top else 0.0
        share_before = running / total
        running += item["expected_marks"]
        # The tier follows the marks already covered, so the section crossing a line stays above it.
        item["tier"] = (
            "must" if share_before < MUST_REVISE_SHARE
            else "should" if share_before < SHOULD_REVISE_SHARE else "optional"
        )
        item["tier_label"] = TIERS[item["tier"]]
    return {
        "summary": blueprint["summary"],
        "items": items,
        "practice_mix": blueprint["practice_mix"],
        "gaps": blueprint["gaps"],
        "stale_questions": blueprint["stale_questions"],
        "schedule": _schedule(items, days) if days else [],
        "days": days,
    }


__all__ = ["MAX_DAYS", "TIERS", "build_plan", "practice_breakdown"]
