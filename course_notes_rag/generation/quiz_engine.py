"""Pydantic-enforced quiz builder and grader.

Each question is generated from one parent chunk with JSON-schema structured output, then
checked: exactly one correct option, valid LaTeX and distractor traps. The model cites the
numbered note sentences ([S#]) that prove the answer; the source quote is assembled from
those exact sentences, so it is verbatim and its page is known. Invalid drafts are sent back
to the model with the list of problems.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, Field, ValidationError, model_validator

from course_notes_rag.generation import prompts
from course_notes_rag.generation.evidence import Evidence, evidence_units, resolve_evidence
from course_notes_rag.generation.latex import validate_latex
from course_notes_rag.generation.llm import LLMClient, LLMError, LLMUnavailableError
from course_notes_rag.models import Block, ParentChunk
from course_notes_rag.retrieval.lexical import tokenize
from course_notes_rag.storage.db import Database
from course_notes_rag.text_utils import normalize_for_match
from course_notes_rag.tracking.mastery import MasteryTracker, selection_weights, weighted_sample

log = logging.getLogger(__name__)

BloomLevel = Literal["Recall", "Conceptual", "Application"]
TrapType = Literal["correct", "sign_inversion", "boundary_violation", "terminology_swap"]
BLOOM_LEVELS: tuple[str, ...] = ("Recall", "Conceptual", "Application")
LABELS = ("A", "B", "C", "D")
DISTRACTOR_TRAPS = {"sign_inversion", "boundary_violation", "terminology_swap"}
MIN_QUOTE_WORDS = 4
# Share of the correct option's terms that must appear in its evidence sentences.
EVIDENCE_MIN_OVERLAP = 0.25


# --- Schemas -------------------------------------------------------------------


class QuizOption(BaseModel):
    label: Literal["A", "B", "C", "D"]
    text: str = Field(description="Option text, using LaTeX $...$ for math.")
    is_correct: bool
    trap_type: TrapType
    misconception_explanation: str = Field(
        description="Why this option is right, or what specific student misconception this distractor targets."
    )


class QuizQuestion(BaseModel):
    question_id: int
    bloom_level: BloomLevel
    question_stem: str
    options: list[QuizOption] = Field(min_length=4, max_length=4)
    hint: str
    source_pdf: str
    source_page: int
    source_quote: str = Field(description="Exact quote from notes proving the answer.")
    parent_id: str
    document_id: str
    section_title: str
    source_bboxes: list[dict[str, float]] = Field(default_factory=list)

    @model_validator(mode="after")
    def _one_correct(self) -> "QuizQuestion":
        if sum(o.is_correct for o in self.options) != 1:
            raise ValueError("exactly one option must be correct")
        if [o.label for o in self.options] != list(LABELS):
            raise ValueError("options must be labelled A, B, C, D in order")
        return self

    @property
    def correct_label(self) -> str:
        return next(o.label for o in self.options if o.is_correct)

    def public(self) -> dict[str, Any]:
        """What the student sees before answering: no correctness or rationales."""
        return {
            "question_id": self.question_id,
            "bloom_level": self.bloom_level,
            "question_stem": self.question_stem,
            "options": [{"label": o.label, "text": o.text} for o in self.options],
            "hint": self.hint,
            "source_pdf": self.source_pdf,
            "source_page": self.source_page,
            "section_title": self.section_title,
            "document_id": self.document_id,
        }


class GeneratedQuiz(BaseModel):
    quiz_id: str
    topic: str
    questions: list[QuizQuestion]


class QuizOptionDraft(BaseModel):
    text: str
    is_correct: bool
    trap_type: TrapType
    misconception_explanation: str


class QuizQuestionDraft(BaseModel):
    """The part of a question the LLM writes; ids, quote and page are filled in by us."""

    bloom_level: BloomLevel
    question_stem: str
    options: list[QuizOptionDraft] = Field(min_length=4, max_length=4)
    hint: str
    evidence_ids: list[int] = Field(
        min_length=1, max_length=3,
        description="The [S#] numbers of the note sentences that prove the correct answer.",
    )


def draft_schema(level: str) -> dict[str, Any]:
    schema = QuizQuestionDraft.model_json_schema()
    schema["properties"]["bloom_level"] = {"type": "string", "enum": [level]}
    return schema


# --- Requests & events -----------------------------------------------------------


class QuizRequestError(ValueError):
    """The quiz request itself is invalid or cannot be served."""


@dataclass
class QuizRequest:
    course_id: str
    topic: str | None = None
    num_questions: int = 5
    bloom_levels: list[str] = field(default_factory=lambda: list(BLOOM_LEVELS))
    document_ids: list[str] | None = None
    adaptive: bool = True
    seed: int | None = None


@dataclass
class QuizEvent:
    type: str
    data: dict[str, Any]


@dataclass
class _Slot:
    parent: ParentChunk
    level: str
    adaptive: bool


# --- Validation helpers ------------------------------------------------------------


def check_draft(
    draft: QuizQuestionDraft, level: str, blocks: list[Block]
) -> tuple[list[str], Evidence | None]:
    problems: list[str] = []
    if draft.bloom_level != level:
        problems.append(f'bloom_level must be "{level}"')
    if not draft.question_stem.strip():
        problems.append("question_stem is empty")
    if not draft.hint.strip():
        problems.append("hint is empty")
    correct = [o for o in draft.options if o.is_correct]
    if len(correct) != 1:
        problems.append(f"exactly one option must have is_correct=true (found {len(correct)})")
    for i, opt in enumerate(draft.options, start=1):
        if not opt.text.strip():
            problems.append(f"option {i} text is empty")
        if not opt.misconception_explanation.strip():
            problems.append(f"option {i} misconception_explanation is empty")
        if opt.is_correct and opt.trap_type != "correct":
            problems.append('the correct option must have trap_type "correct"')
        if not opt.is_correct and opt.trap_type not in DISTRACTOR_TRAPS:
            problems.append(f"option {i} is a distractor but has trap_type {opt.trap_type!r}")
    texts = [normalize_for_match(o.text)[0] for o in draft.options]
    if len(set(texts)) != len(texts):
        problems.append("options must all be different")
    traps = {o.trap_type for o in draft.options if not o.is_correct}
    if len(correct) == 1 and len(traps & DISTRACTOR_TRAPS) < 2:
        problems.append("use at least two different distractor traps")
    fields = [draft.question_stem, draft.hint] + [
        s for o in draft.options for s in (o.text, o.misconception_explanation)
    ]
    for text in fields:
        for err in validate_latex(text):
            problems.append(f"invalid LaTeX: {err}")
    evidence, problem = resolve_evidence(draft.evidence_ids, blocks, MIN_QUOTE_WORDS)
    if problem:
        problems.append(problem)
    elif evidence is not None and len(correct) == 1:
        answer_terms = set(tokenize(correct[0].text))
        overlap = len(answer_terms & set(tokenize(evidence.quote))) / max(1, len(answer_terms))
        if answer_terms and overlap < EVIDENCE_MIN_OVERLAP:
            problems.append(
                "the evidence sentences do not support the correct option; cite the [S#] "
                "sentences that state it"
            )
    return problems, evidence


TRAP_RATIONALE = {
    "sign_inversion": "This reverses a sign, direction or term of what the notes state.",
    "boundary_violation": "This applies the result outside the conditions the notes require.",
    "terminology_swap": "This confuses the concept with a related term from the same lecture.",
}


def _support(answer: str, text: str) -> float:
    terms = set(tokenize(answer))
    return len(terms & set(tokenize(text))) / len(terms) if terms else 0.0


# A model writing LaTeX inside JSON often emits "\frac" or "\text" with a single backslash; JSON
# reads \f, \b, \t, \r and \n as control characters, leaving "<form feed>rac" or "<tab>ext".
_NEWLINE_LATEX = re.compile(r"\n(?=(?:abla|eq|u\b|ot|otin|e\b|ewline|orm)\b)")


def restore_latex_escapes(text: str) -> str:
    text = text.replace("\x0c", "\\f").replace("\x08", "\\b")
    text = re.sub(r"\t(?=[A-Za-z])", r"\\t", text)
    text = re.sub(r"\r(?=[A-Za-z])", r"\\r", text)
    return _NEWLINE_LATEX.sub(r"\\n", text)


def repair_draft(draft: QuizQuestionDraft, blocks: list[Block]) -> tuple[QuizQuestionDraft, list[str]]:
    """Fix labelling slips a small model makes without touching what the question asks.

    * trap labels: the single correct option is labelled "correct"; mislabelled distractors get
      a trap type not used yet;
    * an empty distractor rationale is filled from its trap type;
    * evidence that does not support the correct option is replaced by the [S#] sentence that
      supports it best, if one clears ``EVIDENCE_MIN_OVERLAP`` (otherwise validation rejects it).
    """
    repairs: list[str] = []
    d = draft.model_copy(deep=True)
    fields = [(d, "question_stem"), (d, "hint")] + [
        (o, f) for o in d.options for f in ("text", "misconception_explanation")
    ]
    for obj, name in fields:
        fixed = restore_latex_escapes(getattr(obj, name))
        if fixed != getattr(obj, name):
            setattr(obj, name, fixed)
            if "LaTeX escapes restored" not in repairs:
                repairs.append("LaTeX escapes restored")
    correct = [o for o in d.options if o.is_correct]
    if len(correct) != 1:
        return d, repairs  # which option is right is content, not a label: leave it to validation
    answer = next(o for o in d.options if o.is_correct)
    if answer.trap_type != "correct":
        answer.trap_type = "correct"
        repairs.append("correct option relabelled")
    used = {o.trap_type for o in d.options if not o.is_correct and o.trap_type in DISTRACTOR_TRAPS}
    for opt in d.options:
        if not opt.is_correct and opt.trap_type not in DISTRACTOR_TRAPS:
            free = [t for t in ("sign_inversion", "boundary_violation", "terminology_swap") if t not in used]
            opt.trap_type = (free or ["terminology_swap"])[0]
            used.add(opt.trap_type)
            repairs.append("distractor relabelled")
        if not opt.misconception_explanation.strip():
            opt.misconception_explanation = (
                "This statement matches the lecture notes." if opt.is_correct
                else TRAP_RATIONALE[opt.trap_type]
            )
            repairs.append("rationale filled")
    evidence, problem = resolve_evidence(d.evidence_ids, blocks, MIN_QUOTE_WORDS)
    if problem or evidence is None or _support(answer.text, evidence.quote) < EVIDENCE_MIN_OVERLAP:
        units = [u for u in evidence_units(blocks) if len(u.text.split()) >= MIN_QUOTE_WORDS]
        best = max(units, key=lambda u: _support(answer.text, u.text), default=None)
        if best is not None and _support(answer.text, best.text) >= EVIDENCE_MIN_OVERLAP:
            d.evidence_ids = [best.id]
            repairs.append("evidence re-selected")
    return d, repairs


# --- Engine ------------------------------------------------------------------------


class QuizEngine:
    def __init__(
        self, db: Database, llm: LLMClient, retriever: Any, mastery: MasteryTracker, *,
        max_questions: int = 10, max_retries: int = 2, temperature: float = 0.4,
        adaptive_fraction: float = 0.7, adaptive_offset: float = 1.05,
    ) -> None:
        self.db = db
        self.llm = llm
        self.retriever = retriever
        self.mastery = mastery
        self.max_questions = max_questions
        self.max_retries = max_retries
        self.temperature = temperature
        self.adaptive_fraction = adaptive_fraction
        self.adaptive_offset = adaptive_offset

    # -- request validation & context selection --

    def _validate(self, req: QuizRequest) -> None:
        if not 1 <= req.num_questions <= self.max_questions:
            raise QuizRequestError(f"num_questions must be between 1 and {self.max_questions}")
        if not req.bloom_levels:
            raise QuizRequestError("choose at least one Bloom level")
        bad = [lvl for lvl in req.bloom_levels if lvl not in BLOOM_LEVELS]
        if bad:
            raise QuizRequestError(f"unknown Bloom levels: {bad}")
        if req.topic is not None and len(req.topic) > 300:
            raise QuizRequestError("topic is too long (max 300 characters)")
        self.db.get_course(req.course_id)

    def _candidates(self, req: QuizRequest) -> tuple[list[ParentChunk], bool]:
        """Returns (candidates, ranked_by_relevance)."""
        topic = (req.topic or "").strip()
        if topic:
            top_n = min(15, max(10, req.num_questions * 2))
            result = self.retriever.retrieve(
                topic, req.course_id, document_ids=req.document_ids, top_n=top_n
            )
            if result.not_found:
                raise QuizRequestError(
                    f'"{topic}" was not found in your course notes. Try a different topic.'
                )
            ordered = [c.parent_id for c in result.chunks]
            parents = self.db.get_parents(ordered)
            return [parents[pid] for pid in ordered if pid in parents], True
        parents = self.db.list_parents(req.course_id, req.document_ids)
        if not parents:
            raise QuizRequestError("No processed documents yet. Upload notes in the Library first.")
        return parents, False

    def select_contexts(
        self, candidates: list[ParentChunk], req: QuizRequest, ranked: bool, rng: np.random.Generator
    ) -> list[_Slot]:
        n = req.num_questions
        scores = self.mastery.scores([p.parent_id for p in candidates])
        weights = selection_weights([scores[p.parent_id] for p in candidates], self.adaptive_offset)
        n_adaptive = round(self.adaptive_fraction * n) if req.adaptive else 0
        chosen: list[tuple[ParentChunk, bool]] = [
            (p, True) for p in weighted_sample(candidates, weights, n_adaptive, rng)
        ]
        taken = {p.parent_id for p, _ in chosen}
        rest = [p for p in candidates if p.parent_id not in taken]
        if not ranked:
            rest = [rest[i] for i in rng.permutation(len(rest))]
        for p in rest[: n - len(chosen)]:
            chosen.append((p, False))
        # Fewer sections than questions: reuse sections, still favouring weak ones.
        while len(chosen) < n:
            idx = int(rng.choice(len(candidates), p=weights))
            chosen.append((candidates[idx], req.adaptive))
        order = rng.permutation(len(chosen))
        chosen = [chosen[i] for i in order]
        levels = list(req.bloom_levels)
        return [_Slot(p, levels[i % len(levels)], adaptive) for i, (p, adaptive) in enumerate(chosen)]

    # -- generation --

    def generate(self, req: QuizRequest) -> Iterator[QuizEvent]:
        self._validate(req)
        rng = np.random.default_rng(req.seed)
        candidates, ranked = self._candidates(req)
        slots = self.select_contexts(candidates, req, ranked, rng)
        topic = (req.topic or "").strip() or ("Weak-spot practice" if req.adaptive else "Mixed review")
        quiz_id = self.db.create_quiz(
            req.course_id, topic,
            {"num_questions": req.num_questions, "bloom_levels": req.bloom_levels,
             "adaptive": req.adaptive, "document_ids": req.document_ids},
        )
        yield QuizEvent("quiz", {"quiz_id": quiz_id, "topic": topic, "total": len(slots)})

        made: list[QuizQuestion] = []
        used_parents: set[str] = {s.parent.parent_id for s in slots}
        spares = [p for p in candidates if p.parent_id not in used_parents]
        for index, slot in enumerate(slots, start=1):
            yield QuizEvent("progress", {"index": index, "total": len(slots),
                                         "section": slot.parent.section_title, "level": slot.level})
            question: QuizQuestion | None = None
            for parent in [slot.parent, *spares[:1]]:
                try:
                    question = self._generate_one(
                        len(made) + 1, slot.level, topic, parent, [q.question_stem for q in made],
                        rng,
                    )
                except LLMUnavailableError as exc:
                    yield QuizEvent("error", {"message": str(exc)})
                    yield QuizEvent("done", {"quiz_id": quiz_id, "generated": len(made),
                                             "requested": len(slots)})
                    return
                except LLMError as exc:
                    log.warning("quiz generation failed: %s", exc)
                    question = None
                if question is not None:
                    if parent is not slot.parent:
                        spares.remove(parent)
                    break
            if question is None:
                yield QuizEvent("warning", {"message": f"Skipped question {index}: the model could "
                                            "not produce a valid, grounded question."})
                continue
            made.append(question)
            self.db.add_quiz_question(
                quiz_id, question.question_id, question.parent_id, question.model_dump()
            )
            yield QuizEvent("question", {"question": question.public(), "adaptive": slot.adaptive})
        if not made:
            yield QuizEvent("error", {"message": "Could not generate any valid questions. "
                                      "Try another topic or check that the model is running."})
        yield QuizEvent("done", {"quiz_id": quiz_id, "generated": len(made), "requested": len(slots)})

    def _generate_one(
        self, question_id: int, level: str, topic: str, parent: ParentChunk, avoid: list[str],
        rng: np.random.Generator,
    ) -> QuizQuestion | None:
        messages = prompts.quiz_messages(
            level, topic, parent.pdf_name, parent.section_title, parent.blocks, avoid
        )
        schema = draft_schema(level)
        for attempt in range(self.max_retries + 1):
            # Small models repeat the same flawed draft at a fixed temperature; vary retries.
            temperature = min(1.0, self.temperature + 0.25 * attempt)
            raw = self.llm.chat(messages, temperature=temperature, json_schema=schema)
            try:
                draft = QuizQuestionDraft.model_validate_json(_strip_fences(raw))
            except ValidationError as exc:
                problems = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()][:8]
                messages = [*messages, {"role": "assistant", "content": raw},
                            prompts.quiz_feedback_message(problems)]
                continue
            draft, repairs = repair_draft(draft, parent.blocks)
            if repairs:
                log.info("quiz draft repaired: %s", repairs)
            problems, evidence = check_draft(draft, level, parent.blocks)
            if problems or evidence is None:
                messages = [*messages, {"role": "assistant", "content": raw},
                            prompts.quiz_feedback_message(problems)]
                continue
            return self._finalize(question_id, draft, parent, evidence, rng)
        return None

    def _finalize(
        self, question_id: int, draft: QuizQuestionDraft, parent: ParentChunk, evidence: Evidence,
        rng: np.random.Generator,
    ) -> QuizQuestion:
        order = rng.permutation(4)
        options = [
            QuizOption(label=LABELS[pos], **draft.options[int(i)].model_dump())
            for pos, i in enumerate(order)
        ]
        return QuizQuestion(
            question_id=question_id,
            bloom_level=draft.bloom_level,
            question_stem=draft.question_stem.strip(),
            options=options,
            hint=draft.hint.strip(),
            source_pdf=parent.pdf_name,
            source_page=evidence.page,
            source_quote=evidence.quote,
            parent_id=parent.parent_id,
            document_id=parent.document_id,
            section_title=parent.section_title,
            source_bboxes=[b.to_dict() for b in evidence.bboxes],
        )

    # -- grading --

    def answer(self, quiz_id: str, question_id: int, label: str) -> dict[str, Any]:
        label = (label or "").strip().upper()
        if label not in LABELS:
            raise QuizRequestError("label must be one of A, B, C, D")
        quiz = self.db.get_quiz(quiz_id)
        row = self.db.get_quiz_question(quiz_id, question_id)
        question = QuizQuestion.model_validate(row["payload"])
        attempt_number = self.db.count_attempts(quiz_id, question_id) + 1
        is_correct = label == question.correct_label
        self.db.record_attempt(quiz_id, question_id, question.parent_id, label, is_correct,
                               attempt_number)
        mastery_before = mastery_after = None
        updated = False
        if attempt_number == 1:
            try:
                mastery_before, mastery_after = self.mastery.record(
                    question.parent_id, quiz["course_id"], is_correct
                )
                updated = True
            except sqlite3.IntegrityError:  # the section was deleted after the quiz was made
                log.info("mastery not updated: section %s no longer exists", question.parent_id)
        return {
            "quiz_id": quiz_id,
            "question_id": question_id,
            "selected_label": label,
            "is_correct": is_correct,
            "correct_label": question.correct_label,
            "attempt_number": attempt_number,
            "first_try": attempt_number == 1,
            "mastery_updated": updated,
            "mastery_before": mastery_before,
            "mastery_after": mastery_after,
            "options": [o.model_dump() for o in question.options],
            "source_quote": question.source_quote,
            "source_pdf": question.source_pdf,
            "source_page": question.source_page,
            "source_bboxes": question.source_bboxes,
            "document_id": question.document_id,
            "section_title": question.section_title,
        }

    def get_quiz(self, quiz_id: str) -> dict[str, Any]:
        quiz = self.db.get_quiz(quiz_id)
        attempts = self.db.list_attempts(quiz_id)
        first: dict[int, dict[str, Any]] = {}
        for a in attempts:
            first.setdefault(a["question_id"], a)
        questions = []
        for row in self.db.get_quiz_questions(quiz_id):
            q = QuizQuestion.model_validate(row["payload"])
            item = q.public()
            item["answered"] = q.question_id in first
            item["first_try_correct"] = first.get(q.question_id, {}).get("is_correct")
            questions.append(item)
        answered = [q for q in questions if q["answered"]]
        by_level: dict[str, dict[str, int]] = {}
        for q in answered:
            stats = by_level.setdefault(q["bloom_level"], {"answered": 0, "correct": 0})
            stats["answered"] += 1
            stats["correct"] += int(bool(q["first_try_correct"]))
        return {
            "quiz_id": quiz_id,
            "course_id": quiz["course_id"],
            "topic": quiz["topic"],
            "created_at": quiz["created_at"],
            "questions": questions,
            "score": {
                "answered": len(answered),
                "correct_first_try": sum(bool(q["first_try_correct"]) for q in answered),
                "total": len(questions),
                "by_level": by_level,
            },
        }


def _strip_fences(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    return text


__all__ = [
    "BLOOM_LEVELS", "GeneratedQuiz", "QuizEngine", "QuizEvent", "QuizOption", "QuizQuestion",
    "QuizQuestionDraft", "QuizRequest", "QuizRequestError", "check_draft", "draft_schema",
]
