"""Numbered evidence sentences for quiz generation.

The model is shown the notes as ``[S#]`` sentences and answers with the numbers of the
sentences that prove the correct option. The quote is then assembled from those exact
sentences, so it is verbatim by construction and its page and bounding box are known.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from course_notes_rag.models import BBox, Block
from course_notes_rag.text_utils import split_sentences

_ID_LINE = re.compile(r"^\[S(\d+)\] (.*)$")


@dataclass(frozen=True)
class EvidenceUnit:
    id: int
    text: str
    page: int
    block: Block


@dataclass(frozen=True)
class Evidence:
    quote: str
    page: int
    bboxes: list[BBox]
    ids: list[int]


def evidence_units(blocks: list[Block]) -> list[EvidenceUnit]:
    units: list[EvidenceUnit] = []
    for block in blocks:
        if block.is_heading:
            continue
        if block.is_atomic:
            pieces = [block.to_markdown()]
        else:
            pieces = split_sentences(block.text) or [block.text]
        for piece in pieces:
            piece = piece.strip()
            if piece:
                units.append(EvidenceUnit(len(units) + 1, piece, block.page, block))
    return units


def format_evidence(blocks: list[Block]) -> str:
    """Notes with ``[p. N]`` page markers, headings for context and numbered sentences."""
    by_block: dict[int, list[EvidenceUnit]] = {}
    for unit in evidence_units(blocks):
        by_block.setdefault(id(unit.block), []).append(unit)
    lines: list[str] = []
    page: int | None = None
    for block in blocks:
        if block.page != page:
            lines.append(f"[p. {block.page}]")
            page = block.page
        if block.is_heading:
            lines.append(block.to_markdown())
            continue
        for unit in by_block.get(id(block), []):
            lines.append(f"[S{unit.id}] {unit.text}")
    return "\n".join(lines)


def resolve_evidence(ids: list[int], blocks: list[Block], min_words: int = 4) -> tuple[Evidence | None, str | None]:
    """Turn the model's ``[S#]`` ids into a verbatim quote; returns (evidence, problem)."""
    units = {u.id: u for u in evidence_units(blocks)}
    wanted = sorted(dict.fromkeys(ids))
    unknown = [i for i in wanted if i not in units]
    if not wanted:
        return None, "evidence_ids must list 1 to 3 [S#] sentence numbers"
    if unknown:
        return None, f"evidence_ids {unknown} do not exist; use only the [S#] numbers shown in the notes"
    chosen = [units[i] for i in wanted]
    # Formulas join the quote as inline math ($...$) so they render inside a sentence; the LaTeX
    # itself is unchanged.
    quote = " ".join(
        f"${u.block.text.strip()}$" if u.block.type == "formula" else u.text for u in chosen
    )
    if len(re.sub(r"[$\\{}^_|]", " ", quote).split()) < min_words:
        return None, f"the evidence must contain at least {min_words} words"
    bboxes: list[BBox] = []
    for u in chosen:
        if u.block.bbox is not None and u.block.bbox not in bboxes:
            bboxes.append(u.block.bbox)
    return Evidence(quote, chosen[0].page, bboxes, wanted), None


def parse_numbered_notes(text: str) -> list[tuple[int, str, int]]:
    """Inverse of :func:`format_evidence`: (id, sentence, page) triples. Used by test doubles."""
    out: list[tuple[int, str, int]] = []
    page = 1
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[p. ") and line.endswith("]"):
            page = int(line[4:-1])
            continue
        m = _ID_LINE.match(line)
        if m:
            out.append((int(m.group(1)), m.group(2), page))
    return out
