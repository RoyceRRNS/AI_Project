"""Exam-style mock papers and practice sets with worked solutions, grounded in the notes.

The past papers set the shape: the same parts, marks and either/or choices, and the same question
types in the same places. Each question is then written from one section of the notes, shown to
the model as numbered [S#] sentences. Solution steps cite those numbers, and the
``[Source: file.pdf, p. N]`` citations are built from them, so a citation cannot point at a page
the model made up. Invalid drafts go back with the list of problems, as quiz questions do, and a
numerical answer is recomputed from the model's own expression.
"""

from __future__ import annotations

import ast
import logging
import math
import operator
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from statistics import median
from typing import Any

import numpy as np
from pydantic import BaseModel, Field, ValidationError

from course_notes_rag.generation.citations import format_citation, verify_citations
from course_notes_rag.generation.evidence import EvidenceUnit, evidence_units, resolve_evidence
from course_notes_rag.generation.latex import math_segments, validate_latex
from course_notes_rag.generation.llm import LLMClient, LLMError, LLMUnavailableError
from course_notes_rag.generation.prompts import quiz_feedback_message
from course_notes_rag.generation.quiz_engine import (
    MIN_QUOTE_WORDS, QuizEvent, _strip_fences, restore_latex_escapes,
)
from course_notes_rag.models import BBox, ParentChunk, RetrievedChunk
from course_notes_rag.papers import prompts
from course_notes_rag.papers.blueprint import dice
from course_notes_rag.papers.classify import numbers_in
from course_notes_rag.papers.taxonomy import (
    QUESTION_TYPES, TYPE_LABELS, BloomLevel, QuestionType,
)
from course_notes_rag.retrieval.lexical import tokenize
from course_notes_rag.storage.db import Database
from course_notes_rag.storage.papers import PaperStore

log = logging.getLogger(__name__)

MAX_PAPER_QUESTIONS = 20
MAX_PRACTICE_QUESTIONS = 10
MIN_QUESTION_WORDS = 5
MIN_CASE_WORDS = 25
# Share of a question's terms its cited sentences must contain.
EVIDENCE_MIN_OVERLAP = 0.2
COPY_LIMIT = 0.9
REPEAT_LIMIT = 0.8
ANSWER_REL_TOLERANCE = 0.02
ANSWER_ABS_TOLERANCE = 0.011
MAX_EXPONENT = 12
UNASKED_SECTION_WEIGHT = 0.1
SAME_SECTION_BOOST = 2.0
MIN_SECTION_UNITS = 2
MAX_EXAMPLES = 3
MAX_AVOID = 8

DEFAULT_MARKS = {
    "definition": 2.0, "explain_compare": 5.0, "derivation": 10.0, "numerical": 8.0,
    "algorithm": 8.0, "diagram": 8.0, "case_based": 10.0,
}
_NEEDS_MATH = {"numerical", "derivation"}
_WORKED_TYPES = {"numerical", "algorithm", "derivation"}
_COMPUTED_TYPES = {"numerical", "algorithm"}
_ALGORITHM_HINT = re.compile(
    r"\b(?:algorithm|procedure|update|step|iteration|rule|method|solves?|search|train(?:ing|ed)?)\b",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?(?:\s*/\s*\d+(?:\.\d+)?)?")
_DIAGRAM_WORD = re.compile(r"\b(?:draw|sketch|diagram|label)", re.IGNORECASE)
_INLINE_EVIDENCE = re.compile(r"\s*\[S\d+(?:\s*,\s*S?\d+)*\]")
_LATEX_ARITHMETIC = (
    (re.compile(r"\\frac\{([^{}]+)\}\{([^{}]+)\}"), r"((\1)/(\2))"),
    (re.compile(r"\\times|\\cdot"), "*"),
    (re.compile(r"\\div"), "/"),
    (re.compile(r"\\left|\\right|\\,|\\;|\\!"), ""),
    (re.compile(r"\{"), "("),
    (re.compile(r"\}"), ")"),
)
_INSTRUCTION = re.compile(
    r"^(?:define|explain|describe|discuss|compare|contrast|summari[sz]e|identify|list|state|outline|"
    r"mention|write|set up|use|apply|calculate|compute|verify|show|derive|draw)\b",
    re.IGNORECASE,
)

_BINARY = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
}
_FUNCTIONS = {
    "sqrt": math.sqrt, "log": math.log, "ln": math.log, "log2": math.log2, "log10": math.log10,
    "exp": math.exp, "sin": math.sin, "cos": math.cos, "tan": math.tan, "tanh": math.tanh,
    "abs": abs, "min": min, "max": max, "round": round,
}
_CONSTANTS = {"pi": math.pi, "e": math.e}


class MockRequestError(ValueError):
    """The request cannot be served, for a reason the student can act on."""


class MockStep(BaseModel):
    text: str
    evidence_ids: list[int] = Field(
        default_factory=list, max_length=3,
        description="The [S#] numbers of the note sentences this step uses.",
    )


class MarkPoint(BaseModel):
    point: str
    marks: float


class MockDraft(BaseModel):
    """What the model writes; citations, page numbers and labels are added by us."""

    question: str
    question_type: QuestionType
    bloom_level: BloomLevel
    steps: list[MockStep] = Field(min_length=1, max_length=8)
    final_answer: str
    answer_expression: str = ""
    marking: list[MarkPoint] = Field(default_factory=list, max_length=8)


def draft_schema(question_type: str) -> dict[str, Any]:
    schema = MockDraft.model_json_schema()
    schema["properties"]["question_type"] = {"type": "string", "enum": [question_type]}
    return schema


@dataclass
class MockRequest:
    course_id: str
    kind: str = "practice"
    num_questions: int = 6
    question_types: list[str] | None = None
    topic: str | None = None
    template_paper_id: str | None = None
    seed: int | None = None


@dataclass
class Slot:
    """One question to write: its place in the paper, and what kind of question it must be."""

    label: str
    number: str
    marks: float
    question_type: str
    sub: str = ""
    part: str = ""
    choice_group: str = ""
    source_parent_id: str | None = None
    parents: list[ParentChunk] = field(default_factory=list)


# --- Numerical answers -------------------------------------------------------------------


def safe_eval(expression: str) -> float | None:
    """Evaluate plain arithmetic without ``eval``; anything else gives ``None``."""
    text = (expression or "").strip().replace("^", "**").replace("×", "*").replace("−", "-")
    if not text or len(text) > 300:
        return None
    try:
        return float(_evaluate(ast.parse(text, mode="eval").body))
    except (ValueError, TypeError, ArithmeticError, SyntaxError, RecursionError):
        return None


def _evaluate(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.Name) and node.id in _CONSTANTS:
        return _CONSTANTS[node.id]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        value = _evaluate(node.operand)
        return -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.BinOp):
        left, right = _evaluate(node.left), _evaluate(node.right)
        if isinstance(node.op, ast.Pow):
            if abs(right) > MAX_EXPONENT:
                raise ValueError("exponent too large")
            return left**right
        if type(node.op) in _BINARY:
            return _BINARY[type(node.op)](left, right)
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in _FUNCTIONS and not node.keywords):
        return _FUNCTIONS[node.func.id](*[_evaluate(a) for a in node.args])
    raise ValueError("not plain arithmetic")


def _plain_arithmetic(latex: str) -> str:
    for pattern, replacement in _LATEX_ARITHMETIC:
        latex = pattern.sub(replacement, latex)
    return latex


def arithmetic_slips(text: str) -> list[str]:
    """Equalities between plain numbers that do not hold, such as ``$4 - 0.1 \\cdot 2 = 3.2$``."""
    segments, _errors = math_segments(text)
    slips = []
    for segment in segments or [text]:
        sides = [(side.strip(), safe_eval(_plain_arithmetic(side))) for side in segment.split("=")]
        known = [(side, value) for side, value in sides if value is not None]
        for (left, a), (right, b) in zip(known, known[1:]):
            if not math.isclose(a, b, rel_tol=ANSWER_REL_TOLERANCE, abs_tol=ANSWER_ABS_TOLERANCE):
                slips.append(f"{left} is {a:.6g}, not {right}")
    return slips


def stated_number(text: str) -> float | None:
    """The value a final answer states: the last number, so "Using 2 steps, x = 4" gives 4."""
    found = _NUMBER.findall(re.sub(r"(?<=\d),(?=\d{3}\b)", "", text or ""))
    if not found:
        return None
    last = found[-1]
    if "/" in last:
        top, bottom = last.split("/")
        return float(top) / float(bottom) if float(bottom) else None
    return float(last)


def answers_agree(stated: float, computed: float) -> bool:
    candidates = (stated, stated / 100)  # "95%" and 0.95 are the same answer
    return any(
        math.isclose(c, computed, rel_tol=ANSWER_REL_TOLERANCE, abs_tol=ANSWER_ABS_TOLERANCE)
        for c in candidates
    )


# --- Validation and repair -------------------------------------------------------------------


def _terms(text: str) -> set[str]:
    return {t for t in tokenize(text) if not t.isdigit()}


def _support(text: str, evidence: str) -> float:
    terms = _terms(text)
    return len(terms & _terms(evidence)) / len(terms) if terms else 0.0


def _units(blocks: list) -> dict[int, EvidenceUnit]:
    return {u.id: u for u in evidence_units(blocks)}


def _cited_text(draft: MockDraft, units: dict[int, EvidenceUnit]) -> str:
    return " ".join(units[i].text for s in draft.steps for i in s.evidence_ids if i in units)


def _rescale_marking(draft: MockDraft, marks: float) -> bool:
    points = [p for p in draft.marking if p.marks > 0 and p.point.strip()]
    total = sum(p.marks for p in points)
    if not points or math.isclose(total, marks):
        return False
    scaled = [round(p.marks * marks / total * 2) / 2 for p in points]
    scaled[-1] = max(0.5, scaled[-1] + marks - sum(scaled))
    for point, value in zip(points, scaled):
        point.marks = value
    draft.marking = points
    return True


def repair_mock_draft(draft: MockDraft, slot: Slot, parent: ParentChunk) -> tuple[MockDraft, list[str]]:
    """Fix what a small model gets wrong in form without changing what the question asks."""
    repairs: list[str] = []
    d = draft.model_copy(deep=True)
    for step in d.steps:  # "[S23]" written into the text is a citation in the wrong place
        inline = [int(n) for m in _INLINE_EVIDENCE.findall(step.text) for n in re.findall(r"\d+", m)]
        if inline:
            step.text = _INLINE_EVIDENCE.sub("", step.text)
            step.evidence_ids = list(dict.fromkeys([*step.evidence_ids, *inline]))[:3]
            repairs.append("inline evidence moved")
    d.final_answer = _INLINE_EVIDENCE.sub("", d.final_answer)
    for point in d.marking:
        point.point = _INLINE_EVIDENCE.sub("", point.point)
    fields = [(d, "question"), (d, "final_answer")] + [(s, "text") for s in d.steps]
    fields += [(p, "point") for p in d.marking]
    for obj, name in fields:
        fixed = restore_latex_escapes(getattr(obj, name)).strip()
        if fixed != getattr(obj, name):
            setattr(obj, name, fixed)
            if "text cleaned" not in repairs:
                repairs.append("text cleaned")
    if d.question_type != slot.question_type:
        d.question_type = slot.question_type
        repairs.append("question type relabelled")
    units = _units(parent.blocks)
    for step in d.steps:
        known = [i for i in dict.fromkeys(step.evidence_ids) if i in units]
        if len(known) != len(step.evidence_ids):
            repairs.append("unknown evidence removed")
        step.evidence_ids = known
        if known and _support(step.text, " ".join(units[i].text for i in known)) < EVIDENCE_MIN_OVERLAP:
            step.evidence_ids = []
            repairs.append("unrelated evidence removed")
    if not any(s.evidence_ids for s in d.steps):
        # The model cited nothing usable: find the sentence the solution relies on.
        usable = [u for u in units.values() if len(u.text.split()) >= MIN_QUOTE_WORDS]
        scored = [
            (max((_support(s.text, u.text), j) for j, s in enumerate(d.steps)), u) for u in usable
        ]
        if scored:
            (score, index), unit = max(scored, key=lambda item: item[0])
            if score >= EVIDENCE_MIN_OVERLAP:
                d.steps[index].evidence_ids = [unit.id]
                repairs.append("evidence re-selected")
    computed = safe_eval(d.answer_expression)
    if computed is not None and stated_number(d.final_answer) is None and d.final_answer:
        d.final_answer = f"{d.final_answer} {computed:.4g}"
        repairs.append("final value added")
    if _rescale_marking(d, slot.marks):
        repairs.append("marking scaled")
    return d, list(dict.fromkeys(repairs))


def check_mock_draft(
    draft: MockDraft, slot: Slot, parent: ParentChunk, past: list[str], made: list[str]
) -> list[str]:
    problems: list[str] = []
    question = draft.question.strip()
    words = len(question.split())
    if words < MIN_QUESTION_WORDS:
        problems.append(f"the question must have at least {MIN_QUESTION_WORDS} words")
    if draft.question_type != slot.question_type:
        problems.append(f'question_type must be "{slot.question_type}"')
    if not draft.final_answer.strip():
        problems.append("final_answer is empty")
    if any(not s.text.strip() for s in draft.steps):
        problems.append("every solution step needs text")
    fields = [draft.question, draft.final_answer] + [s.text for s in draft.steps]
    fields += [p.point for p in draft.marking]
    for text in fields:
        for err in validate_latex(text):
            problems.append(f"invalid LaTeX: {err}")
        if "[Source" in text:
            problems.append("do not write [Source: ...] yourself; use evidence_ids")

    kind = slot.question_type
    if kind == "numerical":
        problems.extend(_check_numerical(draft))
    if kind in _WORKED_TYPES and not shows_working(draft):
        problems.append(
            "show the actual working: at least half of the solution steps must work out a new "
            "value or equation, not repeat the question or describe what to do"
        )
    if kind in _COMPUTED_TYPES:
        for step in draft.steps:
            problems.extend(f"recompute this step: {slip}" for slip in arithmetic_slips(step.text))
        stated = stated_number(draft.final_answer)
        if stated is not None and not _reached(stated, draft):
            problems.append(f"final_answer says {stated:g} but no solution step reaches that value")
    if kind in ("algorithm", "derivation") and len(draft.steps) < 2:
        problems.append(f"a {TYPE_LABELS[kind].lower()} needs at least 2 solution steps")
    if is_outline(draft):
        problems.append(
            "write the answer itself: the steps tell the student what to do instead of doing it"
        )
    if kind == "derivation" and not any("$" in s.text or "=" in s.text for s in draft.steps):
        problems.append("a derivation must show the mathematics")
    if kind == "diagram" and not _DIAGRAM_WORD.search(question):
        problems.append("a diagram question must ask the student to draw or label a diagram")
    if kind == "case_based" and words < MIN_CASE_WORDS:
        problems.append(f"a case-based question needs a scenario of at least {MIN_CASE_WORDS} words")

    units = _units(parent.blocks)
    ids = [i for s in draft.steps for i in s.evidence_ids]
    if not ids:
        problems.append("at least one solution step must cite the notes with evidence_ids")
    else:
        unknown = sorted({i for i in ids if i not in units})
        if unknown:
            problems.append(f"evidence_ids {unknown} do not exist; use only the [S#] numbers shown")
        cited = _cited_text(draft, units)
        solution = f"{question} {draft.final_answer}"
        if _support(solution, cited) < EVIDENCE_MIN_OVERLAP:
            problems.append(
                "the cited sentences do not support this question; base it on the [S#] sentences"
            )

    terms = set(tokenize(question))
    for other in past:
        if dice(terms, set(tokenize(other))) >= COPY_LIMIT:
            problems.append("this copies a past question; write a new one in the same style")
            break
    for other in made:
        if dice(terms, set(tokenize(other))) >= REPEAT_LIMIT:
            problems.append("this repeats another question in the paper; ask something different")
            break
    return problems


def _new_working(step: str, question: str) -> bool:
    """Does this step add anything? Equations copied from the question do not count."""
    for segment in math_segments(step)[0]:
        if f"${segment}$" in question:
            step = step.replace(f"${segment}$", " ")
    return "=" in step or bool(set(numbers_in(step)) - set(numbers_in(question)))


def shows_working(draft: MockDraft) -> bool:
    """A worked solution produces values. A subscript such as ``a_1`` is not a value."""
    worked = sum(1 for s in draft.steps if _new_working(s.text, draft.question))
    return worked * 2 >= len(draft.steps)


def _reached(stated: float, draft: MockDraft) -> bool:
    """The final value must come out of the working, not appear only in the answer."""
    values = [float(n) for s in draft.steps for n in numbers_in(s.text)]
    return any(answers_agree(stated, value) for value in values)


def is_outline(draft: MockDraft) -> bool:
    """Most steps are instructions, such as "Define the margin", with nothing worked out."""
    told = sum(1 for s in draft.steps if _INSTRUCTION.match(s.text.strip()) and "=" not in s.text)
    return told * 2 > len(draft.steps)


def _check_numerical(draft: MockDraft) -> list[str]:
    problems: list[str] = []
    given = numbers_in(draft.question)
    if not given:
        problems.append("a numerical question must state its numbers")
    computed = safe_eval(draft.answer_expression)
    if computed is None:
        problems.append("answer_expression must be plain arithmetic giving the final value")
        return problems
    stated = stated_number(draft.final_answer)
    if stated is None:
        problems.append("final_answer must state the computed value")
    elif not answers_agree(stated, computed):
        problems.append(
            f"final_answer says {stated:g} but answer_expression gives {computed:.6g}; recompute both"
        )
    used = {float(n) for n in numbers_in(draft.answer_expression)}
    if given and not used & {float(n) for n in given}:
        problems.append("answer_expression must use the numbers given in the question")
    return problems


# --- Planning the paper ------------------------------------------------------------------------


def suits(parent: ParentChunk, question_type: str) -> bool:
    if len(evidence_units(parent.blocks)) < MIN_SECTION_UNITS:
        return False
    text = parent.text
    if question_type in _NEEDS_MATH:
        return any(b.type == "formula" for b in parent.blocks) or "=" in text
    if question_type == "algorithm":
        return bool(_ALGORITHM_HINT.search(text))
    return True


def template_slots(leaves: list[dict[str, Any]], limit: int) -> list[Slot]:
    """The template paper's own layout, cut at a whole question if it is longer than ``limit``."""
    slots: list[Slot] = []
    for leaf in leaves:
        if len(slots) >= limit and leaf["number"] != slots[-1].number:
            break
        slots.append(Slot(
            label=leaf["label"], number=leaf["number"], sub=leaf.get("sub") or "",
            part=leaf.get("part") or "", choice_group=leaf.get("choice_group") or "",
            marks=float(leaf.get("marks") or DEFAULT_MARKS.get(leaf.get("question_type") or "", 5.0)),
            question_type=leaf.get("question_type") or "explain_compare",
            source_parent_id=leaf.get("parent_id"),
        ))
    return slots


def ensure_applied(slots: list[Slot], can_write: dict[str, bool]) -> None:
    """Every mock needs numerical and application questions, even if the template had few."""
    taken: set[int] = set()
    for needed in ("numerical", "algorithm"):
        if not can_write.get(needed) or len(slots) < 2:
            continue
        if any(s.question_type == needed for s in slots):
            continue
        # Convert the biggest theory question, so the part structure stays the same.
        pool = [
            i for i, s in enumerate(slots)
            if i not in taken and s.question_type in ("explain_compare", "definition", "diagram")
        ] or [i for i in range(len(slots)) if i not in taken]
        if not pool:
            continue
        best = max(pool, key=lambda i: (slots[i].marks, -i))
        slots[best].question_type = needed
        slots[best].source_parent_id = None
        taken.add(best)


def practice_slots(
    count: int, requested: list[str] | None, blueprint: dict[str, Any], questions: list[dict[str, Any]]
) -> list[Slot]:
    if requested:
        kinds = list(requested)
    else:
        by_marks = sorted(blueprint["types"], key=lambda t: -t["marks"])
        kinds = [t["key"] for t in by_marks if t["count"]] or list(QUESTION_TYPES)
        for needed in ("algorithm", "numerical"):
            if needed in kinds:
                kinds.remove(needed)
            kinds.insert(0, needed)
    slots = []
    for i in range(count):
        kind = kinds[i % len(kinds)]
        marks = [q["marks"] for q in questions if q.get("question_type") == kind and q.get("marks")]
        slots.append(Slot(
            label=str(i + 1), number=str(i + 1), question_type=kind,
            marks=float(median(marks)) if marks else DEFAULT_MARKS[kind],
        ))
    return slots


def assign_sections(
    slots: list[Slot], parents: list[ParentChunk], weights: dict[str, float],
    rng: np.random.Generator,
) -> None:
    """Weightage decides where questions come from; no section repeats while another fits."""
    used: set[str] = set()
    floor = max(weights.values(), default=1.0) * UNASKED_SECTION_WEIGHT or UNASKED_SECTION_WEIGHT
    for slot in slots:
        fitting = [p for p in parents if suits(p, slot.question_type)]
        fresh = [p for p in fitting if p.parent_id not in used]
        pool = fresh or fitting or parents
        scores = np.array([
            max(weights.get(p.parent_id, 0.0), floor)
            * (SAME_SECTION_BOOST if p.parent_id == slot.source_parent_id else 1.0)
            for p in pool
        ], dtype=float)
        order = rng.choice(len(pool), size=len(pool), replace=False, p=scores / scores.sum())
        slot.parents = [pool[int(i)] for i in order]
        used.add(slot.parents[0].parent_id)


def _style_examples(slot: Slot, parent: ParentChunk, questions: list[dict[str, Any]]) -> list[str]:
    same = [q for q in questions if q.get("question_type") == slot.question_type]
    pool = same or questions
    pool = sorted(pool, key=lambda q: (
        q.get("parent_id") != parent.parent_id, abs((q.get("marks") or 0) - slot.marks),
    ))
    return [q["text"] for q in pool[:MAX_EXAMPLES]]


# --- Generator ------------------------------------------------------------------------------


class MockGenerator:
    def __init__(
        self, db: Database, store: PaperStore, llm: LLMClient, retriever: Any, *,
        max_retries: int = 2, temperature: float = 0.4,
    ) -> None:
        self.db = db
        self.store = store
        self.llm = llm
        self.retriever = retriever
        self.max_retries = max_retries
        self.temperature = temperature

    def _validate(self, req: MockRequest) -> None:
        if req.kind not in ("paper", "practice"):
            raise MockRequestError('kind must be "paper" or "practice"')
        if req.kind == "practice" and not 1 <= req.num_questions <= MAX_PRACTICE_QUESTIONS:
            raise MockRequestError(f"num_questions must be between 1 and {MAX_PRACTICE_QUESTIONS}")
        unknown = [t for t in req.question_types or [] if t not in QUESTION_TYPES]
        if unknown:
            raise MockRequestError(f"unknown question types: {unknown}")

    def _section_pool(self, req: MockRequest) -> list[ParentChunk]:
        topic = (req.topic or "").strip()
        if not topic:
            return self.db.list_parents(req.course_id)
        result = self.retriever.retrieve(topic, req.course_id, top_n=10, threshold=0.2)
        if result.not_found:
            raise MockRequestError(f'"{topic}" was not found in your course notes. Try a different topic.')
        ordered = [c.parent_id for c in result.chunks]
        found = self.db.get_parents(ordered)
        return [found[pid] for pid in ordered if pid in found]

    def generate(
        self, req: MockRequest, blueprint: dict[str, Any], questions: list[dict[str, Any]],
        course_name: str,
    ) -> Iterator[QuizEvent]:
        self._validate(req)
        if not questions:
            raise MockRequestError("Add a past paper first, so the questions can follow its style.")
        parents = self._section_pool(req)
        if not parents:
            raise MockRequestError("No processed notes yet. Upload notes in the Library first.")
        rng = np.random.default_rng(req.seed)
        slots, spec, title = self._plan(req, blueprint, questions, parents)
        weights = {
            s["parent_id"]: s["expected_marks"] for s in blueprint["sections"] if s.get("parent_id")
        }
        assign_sections(slots, parents, weights, rng)
        spec["max_marks"] = _paper_marks(slots)
        mock_id = self.store.create_mock(req.course_id, req.kind, title, spec)
        total = len(slots)
        yield QuizEvent("mock", {"mock_id": mock_id, "title": title, "kind": req.kind,
                                 "total": total, "spec": spec})
        past = [q["text"] for q in questions]
        made: list[dict[str, Any]] = []
        for index, slot in enumerate(slots, start=1):
            yield QuizEvent("progress", {
                "index": index, "total": total, "label": slot.label,
                "section": slot.parents[0].section_title,
                "question_type": TYPE_LABELS[slot.question_type],
            })
            question: dict[str, Any] | None = None
            for parent in slot.parents[:2]:
                try:
                    question = self._generate_one(
                        slot, parent, course_name, questions, past, [m["question"] for m in made],
                    )
                except LLMUnavailableError as exc:
                    yield QuizEvent("error", {"message": str(exc)})
                    yield QuizEvent("done", {"mock_id": mock_id, "generated": len(made), "requested": total})
                    return
                except LLMError as exc:
                    log.warning("mock question failed: %s", exc)
                if question is not None:
                    break
            if question is None:
                yield QuizEvent("warning", {"message": (
                    f"Skipped question {slot.label}: the model could not write a valid, grounded "
                    f"{TYPE_LABELS[slot.question_type].lower()} question."
                )})
                continue
            question["ord"] = len(made) + 1
            made.append(question)
            self.store.add_mock_question(mock_id, question["ord"], question)
            yield QuizEvent("question", {"question": question})
        if not made:
            yield QuizEvent("error", {"message": "Could not generate any valid questions. "
                                      "Check that the model is running, or try another topic."})
        yield QuizEvent("done", {"mock_id": mock_id, "generated": len(made), "requested": total})

    def _plan(
        self, req: MockRequest, blueprint: dict[str, Any], questions: list[dict[str, Any]],
        parents: list[ParentChunk],
    ) -> tuple[list[Slot], dict[str, Any], str]:
        spec: dict[str, Any] = {"kind": req.kind, "topic": (req.topic or "").strip() or None}
        if req.kind == "practice":
            slots = practice_slots(req.num_questions, req.question_types, blueprint, questions)
            topic = spec["topic"] or "priority topics"
            return slots, spec, f"Practice set: {topic}"
        structures = blueprint["structures"]
        template_id = req.template_paper_id or (blueprint["typical"] or {}).get("paper_id")
        template = next((s for s in structures if s["paper_id"] == template_id), None)
        if template is None:
            raise MockRequestError("That paper is not ready to use as a template.")
        leaves = [q for q in questions if q["paper_id"] == template_id]
        slots = template_slots(leaves, MAX_PAPER_QUESTIONS)
        ensure_applied(slots, {kind: any(suits(p, kind) for p in parents)
                               for kind in ("numerical", "algorithm")})
        spec.update({
            "template_paper_id": template_id, "template_title": template["title"],
            "exam_type": template["exam_type"], "duration": template["duration"],
            "parts": template["parts"],
        })
        return slots, spec, f"Mock paper in the style of {template['title']}"

    def _generate_one(
        self, slot: Slot, parent: ParentChunk, course_name: str, questions: list[dict[str, Any]],
        past: list[str], made: list[str],
    ) -> dict[str, Any] | None:
        avoid = (made + [q["text"] for q in questions if q.get("parent_id") == parent.parent_id])[:MAX_AVOID]
        messages = prompts.mock_messages(
            course_name, slot.question_type, slot.marks, parent.pdf_name, parent.section_title,
            parent.blocks, _style_examples(slot, parent, questions), avoid,
        )
        schema = draft_schema(slot.question_type)
        for attempt in range(self.max_retries + 1):
            temperature = min(1.0, self.temperature + 0.25 * attempt)
            raw = self.llm.chat(messages, temperature=temperature, json_schema=schema)
            try:
                draft = MockDraft.model_validate_json(_strip_fences(raw))
            except ValidationError as exc:
                problems = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()][:8]
                messages = [*messages, {"role": "assistant", "content": raw},
                            quiz_feedback_message(problems)]
                continue
            draft, repairs = repair_mock_draft(draft, slot, parent)
            if repairs:
                log.info("mock draft repaired: %s", repairs)
            problems = check_mock_draft(draft, slot, parent, past, made)
            if problems:
                messages = [*messages, {"role": "assistant", "content": raw},
                            quiz_feedback_message(problems)]
                continue
            return finalize(draft, slot, parent, repairs)
        return None


def _paper_marks(slots: list[Slot]) -> float:
    """What a student actually sits: one side of each OR."""
    seen: dict[tuple[str, str], str] = {}
    total = 0.0
    for slot in slots:
        side = slot.sub.split(".")[0] or slot.number
        if slot.choice_group:
            key = (slot.part, slot.choice_group)
            if seen.setdefault(key, side) != side:
                continue
        total += slot.marks
    return total


def _as_chunk(parent: ParentChunk) -> RetrievedChunk:
    return RetrievedChunk(
        parent_id=parent.parent_id, document_id=parent.document_id, course_id=parent.course_id,
        pdf_name=parent.pdf_name, lecture_title=parent.lecture_title,
        section_title=parent.section_title, blocks=parent.blocks, page_start=parent.page_start,
        page_end=parent.page_end,
    )


def finalize(
    draft: MockDraft, slot: Slot, parent: ParentChunk, repairs: list[str]
) -> dict[str, Any]:
    units = _units(parent.blocks)
    lines = []
    highlights: list[list[BBox]] = []
    for number, step in enumerate(draft.steps, start=1):
        by_page: dict[int, list[BBox]] = {}
        for i in step.evidence_ids:
            boxes = by_page.setdefault(units[i].page, [])
            box = units[i].block.bbox
            if box is not None and box not in boxes:
                boxes.append(box)
        highlights.extend(by_page.values())
        citations = " ".join(format_citation(parent.pdf_name, page) for page in by_page)
        text = step.text.strip()
        lines.append(f"{number}. {text} {citations}".rstrip())
    solution = "\n\n".join(lines)
    report = verify_citations(solution, [_as_chunk(parent)], complete=False)
    # The cited sentences are known exactly, so show those rather than a guess from the wording.
    for citation, boxes in zip(report.citations, highlights):
        citation.page_supported, citation.suggested_page = True, None
        if boxes:
            citation.bboxes = boxes
    ids = sorted({i for s in draft.steps for i in s.evidence_ids})
    evidence, _problem = resolve_evidence(ids, parent.blocks, 1)
    computed = safe_eval(draft.answer_expression)
    return {
        "label": slot.label, "number": slot.number, "sub": slot.sub, "part": slot.part,
        "choice_group": slot.choice_group, "marks": slot.marks,
        "question_type": slot.question_type, "type_label": TYPE_LABELS[slot.question_type],
        "bloom_level": draft.bloom_level, "question": draft.question.strip(), "solution": solution,
        "final_answer": draft.final_answer.strip(),
        "answer_check": (
            {"expression": draft.answer_expression.strip(), "value": computed}
            if slot.question_type == "numerical" and computed is not None else None
        ),
        "marking": [p.model_dump() for p in draft.marking],
        "report": report.to_dict(),
        "section_title": parent.section_title, "lecture_title": parent.lecture_title,
        "parent_id": parent.parent_id, "document_id": parent.document_id,
        "pdf_name": parent.pdf_name,
        "evidence": (
            {"quote": evidence.quote, "page": evidence.page,
             "bboxes": [b.to_dict() for b in evidence.bboxes]}
            if evidence else None
        ),
        "repairs": repairs,
    }


__all__ = [
    "MAX_PAPER_QUESTIONS", "MAX_PRACTICE_QUESTIONS", "MockDraft", "MockGenerator",
    "MockRequest", "MockRequestError", "Slot", "answers_agree", "arithmetic_slips", "assign_sections",
    "check_mock_draft", "draft_schema", "ensure_applied", "finalize", "practice_slots",
    "is_outline", "repair_mock_draft", "safe_eval", "shows_working", "stated_number", "suits",
    "template_slots",
]
