"""Flashcards for every formula and key point in a course's notes.

Each display formula in the notes becomes a card whose back is that formula, copied verbatim
from the parsed PDF. Qwen names the formulas (the fronts) and adds key-point cards for the
definitions, conditions and results worth memorising. A key point must cite the numbered note
sentences ([S#]) that state it and is dropped unless they support it. Formulas never depend on
the model: if it skips one, the card is still made, with the sentence before the formula as
its prompt.

Review uses Leitner boxes: "good" moves a card up a box and pushes its next review further out;
"again" sends it back to box 1, due at once.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from course_notes_rag.generation import prompts
from course_notes_rag.generation.evidence import EvidenceUnit, evidence_units, resolve_evidence
from course_notes_rag.generation.latex import validate_latex
from course_notes_rag.generation.llm import LLMClient, LLMError, LLMUnavailableError
from course_notes_rag.generation.quiz_engine import (
    EVIDENCE_MIN_OVERLAP, MIN_QUOTE_WORDS, _strip_fences, restore_latex_escapes,
)
from course_notes_rag.models import ParentChunk
from course_notes_rag.retrieval.lexical import GREEK, LATEX_ALIASES, SYMBOLS, tokenize
from course_notes_rag.storage.db import Database
from course_notes_rag.text_utils import normalize_for_match

log = logging.getLogger(__name__)

CardKind = Literal["formula", "point"]
REVIEW_RESULTS = ("again", "good")
MAX_BOX = 5
# Days until the next review once a card lands in each box.
BOX_INTERVAL_DAYS = {1: 0, 2: 1, 3: 3, 4: 7, 5: 14}
MAX_POINTS_PER_SECTION = 6
MAX_FRONT_CHARS = 240
MAX_BACK_CHARS = 400
CONTEXT_CHARS = 140
# Formulas shorter than this (without spaces and $) are notation, not something to memorise.
MIN_FORMULA_CHARS = 5
# LaTeX commands that only lay math out, so they may differ from how the notes wrote it.
_LAYOUT_COMMANDS = frozenset({
    "frac", "dfrac", "tfrac", "left", "right", "big", "Big", "bigg", "Bigg", "mathrm", "mathbf", "mathit",
    "mathcal", "boldsymbol", "text", "textbf", "quad", "qquad", "hat", "bar", "tilde", "limits", "displaystyle",
    "cdot", "ldots", "cdots", "dots",
})
_UNICODE_MATH = {**SYMBOLS, **GREEK}
_MATH_SYMBOL = re.compile(r"\\[A-Za-z]+|[=<>" + "".join(_UNICODE_MATH) + "]")


class CardDraft(BaseModel):
    kind: CardKind
    front: str = Field(description="The question on the front of the card.")
    back: str = Field(description="The formula for a formula card; one or two sentences for a key point.")
    evidence_ids: list[int] = Field(
        min_length=1, max_length=3, description="The [S#] numbers of the note lines the card comes from."
    )


class DeckDraft(BaseModel):
    cards: list[CardDraft]


def deck_schema() -> dict[str, Any]:
    return DeckDraft.model_json_schema()


class FlashcardRequestError(ValueError):
    """The flashcard request itself is invalid or cannot be served."""


@dataclass
class FlashcardEvent:
    type: str
    data: dict[str, Any]


@dataclass
class NewCard:
    kind: CardKind
    front: str
    back: str
    source_quote: str
    source_page: int
    bboxes: list[dict[str, float]]
    position: int  # the first [S#] id the card comes from, so cards follow the notes

    @property
    def key(self) -> tuple[str, str]:
        return card_key(self.kind, self.front, self.back)

    def to_row(self) -> dict[str, Any]:
        return {"kind": self.kind, "front": self.front, "back": self.back, "source_quote": self.source_quote,
                "source_page": self.source_page, "bboxes": self.bboxes}


def card_key(kind: str, front: str, back: str) -> tuple[str, str]:
    """Two cards with the same key are duplicates: the same formula, or the same question."""
    text = back if kind == "formula" else front
    return kind, re.sub(r"\s+", "", normalize_for_match(text)[0])


# --- Scheduling ------------------------------------------------------------------


def schedule(box: int, result: str, now: datetime) -> tuple[int, datetime]:
    """The card's new box and when it is next due."""
    if result not in REVIEW_RESULTS:
        raise FlashcardRequestError(f"result must be one of {REVIEW_RESULTS}")
    new_box = 1 if result == "again" else min(MAX_BOX, max(1, box) + 1)
    return new_box, now + timedelta(days=BOX_INTERVAL_DAYS[new_box])


def is_due(due_at: str | None, now: datetime) -> bool:
    return due_at is None or datetime.fromisoformat(due_at) <= now


# --- Card building -----------------------------------------------------------------


def _support(answer: str, text: str) -> float:
    terms = set(tokenize(answer))
    return len(terms & set(tokenize(text))) / len(terms) if terms else 0.0


def _math_symbols(text: str) -> set[str]:
    """Commands, relations and Greek letters, spelled one way whether LaTeX or Unicode."""
    symbols = set()
    for found in _MATH_SYMBOL.findall(text):
        name = found[1:] if found.startswith("\\") else _UNICODE_MATH.get(found, found)
        symbols.add(LATEX_ALIASES.get(name, name))
    return symbols - _LAYOUT_COMMANDS


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?", text))


def _math_is_from_notes(answer: str, evidence: str) -> bool:
    """An answer may only use math symbols and numbers that its evidence uses.

    Word overlap cannot see a slip such as \\neq for \\geq, or a formula the model made up. The
    whole answer is checked, because the model also writes math in plain Unicode (∑ᵢ αᵢ).
    """
    return _math_symbols(answer) <= _math_symbols(evidence) and _numbers(answer) <= _numbers(evidence)


def _squash(latex: str) -> str:
    return re.sub(r"[\s$]", "", latex)


def _trim(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def _parse_drafts(raw: str) -> list[CardDraft] | None:
    """Each card is validated on its own, so one malformed card does not cost the rest."""
    try:
        data = json.loads(_strip_fences(raw))
    except ValueError:
        return None
    items = data.get("cards") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return None
    drafts = []
    for item in items:
        try:
            drafts.append(CardDraft.model_validate(item))
        except ValidationError:
            continue
    return drafts


def _formula_name(front: str, latex: str) -> str | None:
    front = " ".join(restore_latex_escapes(front).split())
    if not front or len(front) > MAX_FRONT_CHARS or validate_latex(front):
        return None
    if "$$" in front or _squash(latex) in _squash(front):  # the front must not give the answer away
        return None
    return front


def _formula_card(unit: EvidenceUnit, units: list[EvidenceUnit], name: str | None, parent: ParentChunk) -> NewCard:
    i = units.index(unit)
    context = units[i - 1] if i > 0 and units[i - 1].block.type != "formula" else None
    if name is None:
        name = (f"State the formula: {_trim(context.text, CONTEXT_CHARS)}" if context
                else f"State the formula from “{parent.section_title}”.")
    # The quote is the lead-in sentence plus the formula; the viewer opens on the formula's page.
    evidence, _ = resolve_evidence([context.id, unit.id] if context else [unit.id], parent.blocks, min_words=0)
    quote = evidence.quote if evidence else unit.block.text
    # The formula's own box goes last: the card shows that area of the page as the PDF has it.
    # Without one, a lead-in box would be mistaken for the formula, so the card keeps none.
    formula_box = unit.block.bbox
    boxes = [] if formula_box is None else [
        *(b for b in (evidence.bboxes if evidence else []) if b.page == unit.page and b != formula_box),
        formula_box,
    ]
    return NewCard("formula", name, unit.block.to_markdown(), quote, unit.page,
                   [b.to_dict() for b in boxes], unit.id)


def _point_card(draft: CardDraft, parent: ParentChunk) -> NewCard | None:
    front = " ".join(restore_latex_escapes(draft.front).split())
    back = restore_latex_escapes(draft.back).strip()
    if not front or not back or len(front) > MAX_FRONT_CHARS or len(back) > MAX_BACK_CHARS:
        return None
    if validate_latex(front) or validate_latex(back):
        return None
    evidence, problem = resolve_evidence(draft.evidence_ids, parent.blocks, MIN_QUOTE_WORDS)
    if problem or evidence is None or _support(back, evidence.quote) < EVIDENCE_MIN_OVERLAP:
        return None
    if not _math_is_from_notes(back, evidence.quote):
        return None
    return NewCard("point", front, back, evidence.quote, evidence.page,
                   [b.to_dict() for b in evidence.bboxes], min(evidence.ids))


# --- Engine --------------------------------------------------------------------------


class FlashcardEngine:
    def __init__(
        self, db: Database, llm: LLMClient, *, temperature: float = 0.3,
        max_points: int = MAX_POINTS_PER_SECTION, clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.db = db
        self.llm = llm
        self.temperature = temperature
        self.max_points = max_points
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def cards_for_section(self, parent: ParentChunk) -> tuple[list[NewCard], list[str]]:
        """Cards for one section plus any warnings. Raises LLMUnavailableError if the model is down."""
        units = evidence_units(parent.blocks)
        formulas = [
            u for u in units if u.block.type == "formula" and len(_squash(u.block.text)) >= MIN_FORMULA_CHARS
        ]
        warnings: list[str] = []
        drafts: list[CardDraft] = []
        try:
            raw = self.llm.chat(
                prompts.flashcard_messages(
                    parent.pdf_name, parent.section_title, parent.blocks, self.max_points, [u.id for u in formulas],
                ),
                temperature=self.temperature, json_schema=deck_schema(),
            )
            parsed = _parse_drafts(raw)
            if parsed is None:
                warnings.append(f"{parent.section_title}: the model's reply was not valid JSON, so only formula cards were made.")
            drafts = parsed or []
        except LLMUnavailableError:
            raise
        except LLMError as exc:
            warnings.append(f"{parent.section_title}: key points skipped ({exc}).")

        by_id = {u.id: u for u in formulas}
        by_latex = {_squash(u.block.text): u.id for u in formulas}
        names: dict[int, str] = {}
        points: list[NewCard] = []
        for draft in drafts:
            # A "point" that only cites formula lines is the model naming a formula.
            if draft.kind == "formula" or all(i in by_id for i in draft.evidence_ids):
                # The model sometimes cites the wrong line but copies the right formula.
                target = next((i for i in draft.evidence_ids if i in by_id), by_latex.get(_squash(draft.back)))
                if target is not None and target not in names:
                    name = _formula_name(draft.front, by_id[target].block.text)
                    if name:
                        names[target] = name
                continue
            card = _point_card(draft, parent)
            if card is not None:
                points.append(card)

        cards = [_formula_card(u, units, names.get(u.id), parent) for u in formulas] + points[: self.max_points]
        cards.sort(key=lambda c: c.position)
        unique: dict[tuple[str, str], NewCard] = {}
        for card in cards:
            unique.setdefault(card.key, card)
        return list(unique.values()), warnings

    def generate(
        self, course_id: str, *, document_ids: list[str] | None = None, regenerate: bool = False,
    ) -> Iterator[FlashcardEvent]:
        """Make cards for every section not generated yet (all chosen sections when regenerating).

        A section counts as done once it ran without problems, even if it held nothing worth a
        card; a section whose key points failed is tried again next time.
        """
        self.db.get_course(course_id)
        parents = self.db.list_parents(course_id, document_ids)
        if not parents:
            raise FlashcardRequestError("No processed documents yet. Upload notes in the Library first.")
        if regenerate:
            self.db.delete_flashcards([p.parent_id for p in parents])
        done = self.db.flashcard_sections_done(course_id)
        todo = [p for p in parents if p.parent_id not in done]
        seen = {card_key(c["kind"], c["front"], c["back"]) for c in self.db.list_flashcards(course_id)}
        yield FlashcardEvent("deck", {"sections": len(todo), "skipped": len(parents) - len(todo)})

        created = 0
        for index, parent in enumerate(todo, start=1):
            yield FlashcardEvent("progress", {"index": index, "total": len(todo), "section": parent.section_title})
            try:
                cards, warnings = self.cards_for_section(parent)
            except LLMUnavailableError as exc:
                yield FlashcardEvent("error", {"message": str(exc)})
                yield FlashcardEvent("done", {"created": created, "sections": index - 1})
                return
            fresh = [c for c in cards if c.key not in seen]
            seen.update(c.key for c in fresh)
            stored = self.db.add_flashcards(parent, [c.to_row() for c in fresh])
            if not warnings:
                self.db.mark_flashcard_section(parent)
            created += len(stored)
            if stored:
                now = self.clock()
                yield FlashcardEvent("cards", {
                    "section": parent.section_title,
                    "cards": [self._public(c, now) for c in stored],
                })
            for message in warnings:
                yield FlashcardEvent("warning", {"message": message})
        yield FlashcardEvent("done", {"created": created, "sections": len(todo)})

    def deck(self, course_id: str) -> dict[str, Any]:
        self.db.get_course(course_id)
        now = self.clock()
        cards = [self._public(c, now) for c in self.db.list_flashcards(course_id)]
        by_box = {str(b): 0 for b in range(1, MAX_BOX + 1)}
        for c in cards:
            by_box[str(c["box"])] += 1
        upcoming = sorted(c["due_at"] for c in cards if not c["due"] and c["due_at"])
        return {
            "course_id": course_id,
            "cards": cards,
            "stats": {
                "total": len(cards),
                "due": sum(c["due"] for c in cards),
                "formulas": sum(c["kind"] == "formula" for c in cards),
                "points": sum(c["kind"] == "point" for c in cards),
                "by_box": by_box,
                "next_due_at": upcoming[0] if upcoming else None,
            },
        }

    def review(self, card_id: str, result: str) -> dict[str, Any]:
        card = self.db.get_flashcard(card_id)
        now = self.clock()
        box, due = schedule(card["box"], result, now)
        updated = self.db.record_flashcard_review(
            card_id, box=box, due_at=due.isoformat(timespec="seconds"), lapse=result == "again"
        )
        return self._public(updated, now)

    @staticmethod
    def _public(card: dict[str, Any], now: datetime) -> dict[str, Any]:
        return {**card, "due": is_due(card["due_at"], now)}
