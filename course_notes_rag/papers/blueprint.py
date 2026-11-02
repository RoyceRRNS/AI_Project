"""The exam blueprint: how a course's past papers are set, computed from their questions.

Marks are counted so that the shares stay honest. The sides of an OR split the marks they stand
for, so a paper's topics add up to the paper's real total rather than counting both options.
"""

from __future__ import annotations

import re
from collections import Counter
from statistics import median
from typing import Any

import numpy as np

from course_notes_rag.papers.classify import numbers_in
from course_notes_rag.papers.taxonomy import (
    BLOOM_LEVELS, PRACTICE_LABELS, PRACTICE_MODES, QUESTION_TYPES, TYPE_LABELS, UNMAPPED_MODULE,
)
from course_notes_rag.retrieval.encoders import Embedder
from course_notes_rag.retrieval.lexical import tokenize

# Older papers count for less: 0.8 per year behind the newest paper.
RECENCY_DECAY = 0.8
UNDATED_AGE = 2
TOP_PHRASINGS = 12
EXAMPLES_PER_PHRASING = 3

# Two questions are "the same question" at these similarities. Shared words alone are weak
# evidence: "Define the margin" and "Prove the margin doubles" use the same words and ask
# different things, so below SAME_WORDS the question type has to agree too.
SAME_WORDS = 0.9
SAME_DICE = 0.7
SAME_COSINE = 0.92
SAME_SECTION_DICE = 0.55
SAME_SECTION_COSINE = 0.8

_COMMAND_TERMS = frozenset(
    """define state list explain describe discuss compare contrast differentiate distinguish derive
    prove show compute calculate evaluate determine find draw sketch trace apply illustrate write
    short notes note meant mean neat diagram suitable briefly detail step steps justify
    answer given following consider obtain give""".split()
)

_PHRASINGS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^(?:differentiate|distinguish)\b"), "Differentiate between … and …"),
    (re.compile(r"^(?:compare|contrast)\b"), "Compare … and …"),
    (re.compile(r"^explain\b.*\b(?:diagram|sketch|figure)\b"), "Explain … with a neat diagram"),
    (re.compile(r"^explain\b.*\bexamples?\b"), "Explain … with an example"),
    (re.compile(r"^explain\b.*\b(?:compute|calculate|find)\b"), "Explain … and compute …"),
    (re.compile(r"^explain\b.*\bshow\b"), "Explain … and show …"),
    (re.compile(r"^explain\b"), "Explain …"),
    (re.compile(r"^(?:draw|sketch)\b"), "Draw … and explain …"),
    (re.compile(r"^derive\b"), "Derive …"),
    (re.compile(r"^(?:prove|show that)\b"), "Prove that …"),
    (re.compile(r"^trace\b"), "Trace … step by step"),
    (re.compile(r"^(?:apply|use|using)\b"), "Apply … to …"),
    (re.compile(r"^(?:calculate|compute)\b"), "Compute … for the given values"),
    (re.compile(r"\b(?:calculate|compute)\b"), "Given …, compute …"),
    (re.compile(r"^(?:find|determine)\b"), "Find …"),
    (re.compile(r"^define\b"), "Define …"),
    (re.compile(r"^what is meant by\b"), "What is meant by …?"),
    (re.compile(r"^what (?:is|are)\b"), "What is …?"),
    (re.compile(r"^(?:state|list|name|mention|enumerate)\b"), "State / list …"),
    (re.compile(r"^write\b.*\bnotes?\b"), "Write short notes on …"),
    (re.compile(r"^(?:discuss|describe)\b"), "Describe …"),
]


def phrasing_of(text: str, question_type: str | None = None) -> str:
    """The examiner's template behind a question, such as "Explain … with a neat diagram"."""
    lowered = " ".join(text.lower().split())
    if question_type == "case_based":
        return "A scenario, then what would you recommend and why"
    for pattern, phrasing in _PHRASINGS:
        if pattern.search(lowered):
            return phrasing
    words = text.split()[:2]
    return f"{' '.join(words).rstrip('.,:;?')} …" if words else "…"


def content_terms(text: str) -> set[str]:
    return {t for t in tokenize(text) if t not in _COMMAND_TERMS and not t.isdigit()}


def dice(a: set[str], b: set[str]) -> float:
    return 2 * len(a & b) / (len(a) + len(b)) if a and b else 0.0


def question_shares(questions: list[dict[str, Any]]) -> dict[str, float]:
    """The share of a question's marks that counts: 1 divided by the sides of its OR."""
    sides: dict[tuple[str, str, str], set[str]] = {}
    for q in questions:
        if q.get("choice_group"):
            key = (q["paper_id"], q.get("part") or "", q["choice_group"])
            sides.setdefault(key, set()).add(_side(q))
    shares = {}
    for q in questions:
        key = (q["paper_id"], q.get("part") or "", q.get("choice_group") or "")
        shares[q["id"]] = 1 / len(sides[key]) if key in sides else 1.0
    return shares


def _side(q: dict[str, Any]) -> str:
    return (q.get("sub") or "").split(".")[0] or q["number"]


def paper_weights(papers: list[dict[str, Any]]) -> dict[str, float]:
    years = [p["year"] for p in papers if p.get("year")]
    newest = max(years, default=None)
    weights = {}
    for paper in papers:
        age = newest - paper["year"] if newest and paper.get("year") else UNDATED_AGE
        weights[paper["id"]] = RECENCY_DECAY ** age
    return weights


# --- Structure ---------------------------------------------------------------------------


def paper_structure(paper: dict[str, Any], questions: list[dict[str, Any]]) -> dict[str, Any]:
    stated = {p["name"]: p for p in paper.get("parts") or []}
    names = list(dict.fromkeys(q.get("part") or "" for q in questions))
    parts = []
    for name in names:
        members = [q for q in questions if (q.get("part") or "") == name]
        units: dict[str, list[dict[str, Any]]] = {}
        for q in members:
            units.setdefault(q["choice_group"] or q["number"], []).append(q)
        unit_marks = []
        for leaves in units.values():
            first_side = _side(leaves[0])
            chosen = [q for q in leaves if _side(q) == first_side] if leaves[0]["choice_group"] else leaves
            unit_marks.append(sum(q.get("marks") or 0 for q in chosen))
        info = stated.get(name, {})
        wanted = info.get("answer_count")
        required = wanted if wanted and 0 < wanted < len(units) else len(units)
        known = [m for m in unit_marks if m]
        each = Counter(known).most_common(1)[0][0] if known else None
        either_or = any(q["choice_group"] for q in members)
        choice = required < len(units)
        total = sum(sorted(unit_marks, reverse=True)[:required])
        pattern = f"{required} × {each:g}" if each else f"{required} questions"
        if len(set(known)) > 1:
            pattern = f"{required} questions, {min(known):g} to {max(known):g} marks each"
        parts.append({
            "name": name, "instructions": info.get("instructions") or "", "questions": len(units),
            "required": required, "marks_each": each, "total": total, "either_or": either_or,
            "choice": choice, "pattern": pattern,
        })
    signature = "|".join(
        f"{p['name']}:{p['pattern']}{':or' if p['either_or'] else ''}" for p in parts
    )
    return {
        "paper_id": paper["id"], "title": paper.get("title") or paper["pdf_name"],
        "exam_type": paper.get("exam_type"), "year": paper.get("year"),
        "max_marks": paper.get("max_marks"), "duration": paper.get("duration"),
        "total_marks": sum(p["total"] for p in parts), "parts": parts, "signature": signature,
    }


def _typical(structures: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not structures:
        return None
    counts = Counter(s["signature"] for s in structures)
    best = max(
        structures,
        key=lambda s: (counts[s["signature"]], s["total_marks"], s.get("year") or 0),
    )
    return {**best, "paper_count": counts[best["signature"]]}


# --- Recurring questions -------------------------------------------------------------------


def find_recurring(
    questions: list[dict[str, Any]], embedder: Embedder | None, papers: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Clusters of the same question asked in different papers, biggest first.

    Each cluster grows around one leader, newest paper first. Comparing against the leader rather
    than any member stops a chain of near neighbours from gluing different questions together.
    """
    if len(questions) < 2:
        return []
    ordered = sorted(questions, key=lambda q: -(papers[q["paper_id"]].get("year") or 0))
    texts = [f"{q.get('context') or ''} {q['text']}".strip() for q in ordered]
    terms = [content_terms(t) for t in texts]
    cosine = None
    if embedder is not None:
        vectors = np.asarray(embedder.encode(texts), dtype=np.float32)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        vectors = vectors / np.maximum(norms, 1e-9)
        cosine = vectors @ vectors.T

    def similarity(i: int, j: int) -> float:
        """How alike two questions are, or 0 if they do not count as the same question."""
        overlap = dice(terms[i], terms[j])
        sim = float(cosine[i, j]) if cosine is not None else 0.0
        a, b = ordered[i], ordered[j]
        same_topic = bool(a.get("parent_id")) and a.get("parent_id") == b.get("parent_id")
        same_kind = a.get("question_type") == b.get("question_type")
        if overlap >= SAME_WORDS or sim >= SAME_COSINE:
            return max(overlap, sim)
        if same_kind and overlap >= SAME_DICE:
            return max(overlap, sim)
        if same_topic and same_kind and (overlap >= SAME_SECTION_DICE or sim >= SAME_SECTION_COSINE):
            return max(overlap, sim)
        return 0.0

    leaders: list[int] = []
    clusters: dict[int, list[dict[str, Any]]] = {}
    for i, q in enumerate(ordered):
        fits = [
            (similarity(i, leader), leader) for leader in leaders
            if all(m["paper_id"] != q["paper_id"] for m in clusters[leader])
        ]
        score, best = max(fits, default=(0.0, -1))
        if score > 0:
            clusters[best].append(q)
        else:
            leaders.append(i)
            clusters[i] = [q]
    out = []
    for members in clusters.values():
        paper_ids = {q["paper_id"] for q in members}
        if len(paper_ids) < 2:
            continue
        members.sort(key=lambda q: papers[q["paper_id"]].get("year") or 0)
        latest = members[-1]
        marks = [q["marks"] for q in members if q.get("marks")]
        types = Counter(q.get("question_type") for q in members if q.get("question_type"))
        years = sorted({papers[q["paper_id"]]["year"] for q in members if papers[q["paper_id"]].get("year")})
        out.append({
            "text": latest["text"], "section_title": latest.get("section_title"),
            "parent_id": latest.get("parent_id"),
            "question_type": types.most_common(1)[0][0] if types else None,
            "marks": median(marks) if marks else None, "papers": len(paper_ids), "years": years,
            "instances": [_example(q, papers) for q in members],
        })
    out.sort(key=lambda c: (-c["papers"], -(c["marks"] or 0), c["text"]))
    return out


def _example(q: dict[str, Any], papers: dict[str, dict[str, Any]]) -> dict[str, Any]:
    paper = papers[q["paper_id"]]
    return {
        "question_id": q["id"], "paper_id": q["paper_id"],
        "paper_title": paper.get("title") or paper["pdf_name"], "year": paper.get("year"),
        "label": q["label"], "part": q.get("part") or "", "text": q["text"],
        "context": q.get("context") or "", "marks": q.get("marks"), "page": q.get("page"),
    }


def find_phrasings(
    questions: list[dict[str, Any]], papers: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for q in questions:
        groups.setdefault(phrasing_of(q["text"], q.get("question_type")), []).append(q)
    out = []
    for phrasing, members in groups.items():
        by_paper: dict[str, dict[str, Any]] = {}
        for q in sorted(members, key=lambda m: -(papers[m["paper_id"]].get("year") or 0)):
            by_paper.setdefault(q["paper_id"], q)
        examples = list(by_paper.values())[:EXAMPLES_PER_PHRASING]
        for q in members:
            if len(examples) >= EXAMPLES_PER_PHRASING:
                break
            if q not in examples:
                examples.append(q)
        types = Counter(q.get("question_type") for q in members if q.get("question_type"))
        marks = [q["marks"] for q in members if q.get("marks")]
        out.append({
            "phrasing": phrasing, "count": len(members),
            "question_type": types.most_common(1)[0][0] if types else None,
            "typical_marks": median(marks) if marks else None,
            "examples": [_example(q, papers) for q in examples],
        })
    out.sort(key=lambda p: (-p["count"], p["phrasing"]))
    return out[:TOP_PHRASINGS]


# --- Topic weightage --------------------------------------------------------------------------


def _topic_rows(
    questions: list[dict[str, Any]], shares: dict[str, float], weights: dict[str, float],
    papers: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    paper_count = max(1, len(papers))
    total_weight = sum(weights.values()) or 1.0
    total_marks = sum((q.get("marks") or 0) * shares[q["id"]] for q in questions) or 1.0
    modules: dict[str, dict[str, Any]] = {}
    sections: dict[str, dict[str, Any]] = {}

    def bump(row: dict[str, Any], q: dict[str, Any], marks: float) -> None:
        row["marks"] += marks
        row["weighted"] += marks * weights[q["paper_id"]]
        row["questions"] += 1
        row["paper_ids"].add(q["paper_id"])
        year = papers[q["paper_id"]].get("year")
        if year:
            row["years"].add(year)
        kind = q.get("question_type")
        if kind:
            row["types"][kind] = row["types"].get(kind, 0.0) + marks

    def blank(**extra: Any) -> dict[str, Any]:
        return {"marks": 0.0, "weighted": 0.0, "questions": 0, "paper_ids": set(), "years": set(),
                "types": {}, **extra}

    for q in questions:
        marks = (q.get("marks") or 0) * shares[q["id"]]
        title = q.get("section_title")
        name = (q.get("lecture_title") or q.get("notes_pdf") or UNMAPPED_MODULE) if title else UNMAPPED_MODULE
        module = modules.setdefault(name, blank(
            module=name, document_id=q.get("document_id") if title else None,
            notes_pdf=q.get("notes_pdf") if title else None, mapped=bool(title),
        ))
        bump(module, q, marks)
        if not title:
            continue
        key = q.get("parent_id") or f"{q.get('notes_pdf')}::{title}"
        section = sections.setdefault(key, blank(
            parent_id=q.get("parent_id"), section_title=title, module=name,
            notes_pdf=q.get("notes_pdf"), document_id=q.get("document_id"),
            page_start=q.get("page_start"), page_end=q.get("page_end"), past_questions=[],
        ))
        bump(section, q, marks)
        section["past_questions"].append(q)

    def finish(row: dict[str, Any]) -> dict[str, Any]:
        paper_ids = row.pop("paper_ids")
        weighted = row.pop("weighted")
        row.update({
            "marks": round(row["marks"], 2), "share": round(row["marks"] / total_marks, 4),
            "marks_per_paper": round(row["marks"] / paper_count, 2),
            "expected_marks": round(weighted / total_weight, 2),
            "papers": len(paper_ids), "frequency": round(len(paper_ids) / paper_count, 4),
            "years": sorted(row["years"]),
            "types": {k: round(v, 2) for k, v in sorted(row["types"].items(), key=lambda kv: -kv[1])},
        })
        return row

    module_rows = sorted((finish(m) for m in modules.values()), key=lambda r: -r["marks"])
    section_rows = sorted((finish(s) for s in sections.values()),
                          key=lambda r: (-r["marks"], -r["papers"]))
    for row in section_rows:
        past = row.pop("past_questions")
        past.sort(key=lambda q: (-(papers[q["paper_id"]].get("year") or 0), -(q.get("marks") or 0)))
        row["examples"] = [_example(q, papers) for q in past[:3]]
    return module_rows, section_rows


# --- Entry point ----------------------------------------------------------------------------


def build_blueprint(
    papers: list[dict[str, Any]], questions: list[dict[str, Any]], embedder: Embedder | None = None
) -> dict[str, Any]:
    by_id = {p["id"]: p for p in papers}
    questions = [q for q in questions if q["paper_id"] in by_id]
    shares = question_shares(questions)
    weights = paper_weights(papers)
    effective = {q["id"]: (q.get("marks") or 0) * shares[q["id"]] for q in questions}
    total_marks = sum(effective.values())
    modules, sections = _topic_rows(questions, shares, weights, by_id)

    def mix(key: str, names: tuple[str, ...]) -> list[dict[str, Any]]:
        rows = []
        for name in names:
            members = [q for q in questions if q.get(key) == name]
            marks = sum(effective[q["id"]] for q in members)
            rows.append({"key": name, "count": len(members), "marks": round(marks, 2),
                         "share": round(marks / total_marks, 4) if total_marks else 0.0})
        return rows

    types = mix("question_type", QUESTION_TYPES)
    for row in types:
        row["label"] = TYPE_LABELS[row["key"]]
    marks_counts = Counter(q["marks"] for q in questions if q.get("marks"))
    structures = [
        paper_structure(p, [q for q in questions if q["paper_id"] == p["id"]])
        for p in papers if any(q["paper_id"] == p["id"] for q in questions)
    ]
    practice: dict[str, float] = {}
    for q in questions:
        mode = PRACTICE_MODES.get(q.get("question_type") or "")
        if mode:
            practice[mode] = practice.get(mode, 0.0) + effective[q["id"]]
    unmapped = [q for q in questions if not q.get("section_title")]
    stale = sum(1 for q in questions if q.get("section_title") and not q.get("parent_id"))
    mapped_marks = sum(effective[q["id"]] for q in questions if q.get("section_title"))
    years = sorted({p["year"] for p in papers if p.get("year")})
    return {
        "summary": {
            "papers": len(papers), "questions": len(questions), "years": years,
            "marks": round(total_marks, 2),
            "marks_per_paper": round(total_marks / len(papers), 2) if papers else 0.0,
            "coverage": round(mapped_marks / total_marks, 4) if total_marks else 0.0,
            "numericals": sum(1 for q in questions if numbers_in(q["text"]) and q.get("question_type") == "numerical"),
            "unmarked": sum(1 for q in questions if not q.get("marks")),
        },
        "modules": modules,
        "sections": sections,
        "types": types,
        "bloom": mix("bloom_level", BLOOM_LEVELS),
        "marks_distribution": [{"marks": m, "count": n} for m, n in sorted(marks_counts.items())],
        "structures": structures,
        "typical": _typical(structures),
        "recurring": find_recurring(questions, embedder, by_id),
        "phrasings": find_phrasings(questions, by_id),
        "practice_mix": [
            {"mode": mode, "label": PRACTICE_LABELS[mode], "marks": round(marks, 2),
             "share": round(marks / total_marks, 4) if total_marks else 0.0}
            for mode, marks in sorted(practice.items(), key=lambda kv: -kv[1])
        ],
        "gaps": [_example(q, by_id) for q in unmapped],
        "stale_questions": stale,
    }


__all__ = [
    "build_blueprint", "content_terms", "dice", "find_phrasings", "find_recurring",
    "paper_structure", "paper_weights", "phrasing_of", "question_shares",
]
