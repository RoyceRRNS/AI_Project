"""Parse and verify ``[Source: Lecture_04.pdf, p. 12]`` citations against retrieved context."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Any

from course_notes_rag.models import BBox, RetrievedChunk
from course_notes_rag.text_utils import split_sentences

_BRACKET = re.compile(r"\[(\s*Source\s*:[^\[\]]+)\]", re.IGNORECASE)
_PART = re.compile(
    r"Source\s*:\s*(?P<pdf>.+?)\s*,\s*(?:pages?|pp?)\.?\s*(?P<p1>\d+)(?:\s*[-–]\s*(?P<p2>\d+))?\s*$",
    re.IGNORECASE,
)
_MIN_CLAIM_WORDS = 6


@dataclass
class Citation:
    raw: str
    pdf_name: str
    page: int
    page_end: int
    start: int
    end: int
    valid: bool = False
    reason: str | None = None
    document_id: str | None = None
    parent_id: str | None = None
    resolved_pdf: str | None = None
    bboxes: list[BBox] = field(default_factory=list)
    page_supported: bool = True
    suggested_page: int | None = None
    inferred: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw": self.raw,
            "pdf_name": self.pdf_name,
            "page": self.page,
            "page_end": self.page_end,
            "start": self.start,
            "end": self.end,
            "valid": self.valid,
            "reason": self.reason,
            "document_id": self.document_id,
            "parent_id": self.parent_id,
            "resolved_pdf": self.resolved_pdf,
            "bboxes": [b.to_dict() for b in self.bboxes],
            "page_supported": self.page_supported,
            "suggested_page": self.suggested_page,
            "inferred": self.inferred,
        }


@dataclass
class CitationReport:
    citations: list[Citation]
    uncited_sentences: list[str]
    claim_count: int
    annotated_answer: str | None = None

    @property
    def inferred_count(self) -> int:
        return sum(c.inferred for c in self.citations)

    @property
    def valid_count(self) -> int:
        return sum(c.valid for c in self.citations)

    @property
    def invalid_count(self) -> int:
        return sum(not c.valid for c in self.citations)

    @property
    def page_mismatch_count(self) -> int:
        return sum(c.valid and not c.page_supported for c in self.citations)

    @property
    def coverage(self) -> float:
        if self.claim_count == 0:
            return 1.0
        return 1.0 - len(self.uncited_sentences) / self.claim_count

    @property
    def grounded(self) -> bool:
        return (bool(self.citations) and self.invalid_count == 0 and not self.uncited_sentences
                and self.page_mismatch_count == 0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "citations": [c.to_dict() for c in self.citations],
            "valid_count": self.valid_count,
            "invalid_count": self.invalid_count,
            "page_mismatch_count": self.page_mismatch_count,
            "inferred_count": self.inferred_count,
            "annotated_answer": self.annotated_answer,
            "uncited_sentences": self.uncited_sentences,
            "claim_count": self.claim_count,
            "coverage": round(self.coverage, 3),
            "grounded": self.grounded,
        }


def format_citation(pdf_name: str, page: int) -> str:
    return f"[Source: {pdf_name}, p. {page}]"


def _norm_pdf(name: str) -> str:
    stem = PurePath(name.strip().strip("'\"`")).name
    if stem.lower().endswith(".pdf"):
        stem = stem[:-4]
    return re.sub(r"[\s_\-]+", "", stem).casefold()


def parse_citations(text: str) -> list[Citation]:
    citations: list[Citation] = []
    for match in _BRACKET.finditer(text):
        for part in re.split(r";", match.group(1)):
            part = part.strip()
            if not part:
                continue
            if not part.lower().startswith("source"):
                part = f"Source: {part}"
            m = _PART.match(part)
            if not m:
                citations.append(
                    Citation(match.group(0), part, 0, 0, match.start(), match.end(),
                             reason="malformed")
                )
                continue
            p1 = int(m.group("p1"))
            p2 = int(m.group("p2")) if m.group("p2") else p1
            citations.append(
                Citation(match.group(0), m.group("pdf").strip(), p1, max(p1, p2),
                         match.start(), match.end())
            )
    return citations


INFER_MIN = 0.5  # share of a sentence's terms one passage must contain to auto-cite it


def verify_citations(
    answer: str, chunks: list[RetrievedChunk], *, complete: bool = True
) -> CitationReport:
    """Check every citation against the retrieved context.

    With ``complete=True``, sentences the model left uncited are matched against the retrieved
    passages: a sentence whose terms are mostly found in one passage gets an *inferred*
    citation (inserted into ``annotated_answer``); the rest are reported as unsupported.
    """
    report = _verify(answer, chunks)
    if not complete or not report.uncited_sentences:
        return report
    insertions: list[tuple[int, str]] = []
    for sentence in report.uncited_sentences:
        support = _best_support(sentence, chunks)
        pos = answer.find(sentence)
        if support is None or pos < 0:
            continue
        chunk, page = support
        end = pos + len(sentence)
        at = end - 1 if answer[end - 1 : end] in (".", "!", "?") else end
        insertions.append((at, f" {format_citation(chunk.pdf_name, page)}"))
    if not insertions:
        return report
    annotated, starts, offset = answer, set(), 0
    for at, text in sorted(insertions):
        real = at + offset
        annotated = annotated[:real] + text + annotated[real:]
        starts.add(real + 1)
        offset += len(text)
    final = _verify(annotated, chunks)
    for cit in final.citations:
        cit.inferred = cit.start in starts
    final.annotated_answer = annotated
    return final


def _best_support(sentence: str, chunks: list[RetrievedChunk]) -> tuple[RetrievedChunk, int] | None:
    from course_notes_rag.retrieval.lexical import tokenize

    terms = set(tokenize(_BRACKET.sub("", sentence)))
    if len(terms) < 3:
        return None
    best: tuple[float, RetrievedChunk, int] | None = None
    for chunk in chunks:
        for block in chunk.blocks:
            if block.is_heading:
                continue
            score = len(terms & set(tokenize(block.text))) / len(terms)
            if best is None or score > best[0]:
                best = (score, chunk, block.page)
    if best is None or best[0] < INFER_MIN:
        return None
    return best[1], best[2]


def _verify(answer: str, chunks: list[RetrievedChunk]) -> CitationReport:
    citations = parse_citations(answer)
    by_pdf: dict[str, list[RetrievedChunk]] = {}
    for chunk in chunks:
        by_pdf.setdefault(_norm_pdf(chunk.pdf_name), []).append(chunk)
    for cit in citations:
        if cit.reason == "malformed":
            continue
        candidates = by_pdf.get(_norm_pdf(cit.pdf_name))
        if not candidates:
            cit.reason = "unknown_source"
            continue
        hit = next(
            (c for c in candidates if c.page_start <= cit.page and cit.page_end <= c.page_end),
            None,
        )
        if hit is None:
            cit.reason = "page_not_in_context"
            continue
        cit.valid = True
        cit.document_id = hit.document_id
        cit.parent_id = hit.parent_id
        cit.resolved_pdf = hit.pdf_name
        _locate_claim(cit, _claim_before(answer, cit.start), hit)
    uncited, claims = _uncited_claims(answer)
    return CitationReport(citations, uncited, claims)


def _claim_before(answer: str, start: int) -> str:
    """The sentence a citation closes: text back to the previous sentence end or citation."""
    head = answer[:start]
    cut = max(head.rfind("]"), head.rfind(". "), head.rfind("\n\n"), head.rfind("$$"))
    return head[cut + 1 :].strip() if cut >= 0 else head.strip()


SUPPORT_MIN = 0.2       # share of claim terms a block needs to count as the supporting paragraph
MISPAGE_STRONG = 0.5    # another page must match at least this well ...
MISPAGE_MARGIN = 0.25   # ... and this much better than the cited page to flag a wrong page


def _locate_claim(cit: Citation, claim: str, chunk: RetrievedChunk) -> None:
    """Highlight the block that supports the claim and check that it is on the cited page.

    Sets ``cit.bboxes`` and, when the claim clearly lives on a different page of the same
    section, ``cit.page_supported = False`` with ``cit.suggested_page`` pointing to it.
    """
    from course_notes_rag.retrieval.lexical import tokenize

    claim_tokens = set(tokenize(claim))
    best_by_page: dict[int, tuple[float, BBox | None]] = {}
    for block in chunk.blocks:
        if block.bbox is None or block.is_heading or not claim_tokens:
            continue
        block_tokens = set(tokenize(block.text))
        if not block_tokens:
            continue
        score = len(claim_tokens & block_tokens) / len(claim_tokens)
        if score > best_by_page.get(block.page, (0.0, None))[0]:
            best_by_page[block.page] = (score, block.bbox)

    on_page, on_page_box = best_by_page.get(cit.page, (0.0, None))
    elsewhere = max(
        ((s, p, b) for p, (s, b) in best_by_page.items() if p != cit.page),
        default=(0.0, None, None), key=lambda t: t[0],
    )
    if elsewhere[0] >= MISPAGE_STRONG and elsewhere[0] - on_page >= MISPAGE_MARGIN:
        cit.page_supported = False
        cit.suggested_page = elsewhere[1]
        cit.bboxes = [elsewhere[2]]
        return
    if on_page_box is not None and on_page >= SUPPORT_MIN:
        cit.bboxes = [on_page_box]
    else:
        cit.bboxes = chunk.bboxes_on_page(cit.page)


def _uncited_claims(answer: str) -> tuple[list[str], int]:
    """Prose sentences of 6+ words that carry no citation of their own."""
    uncited: list[str] = []
    claims = 0
    for paragraph in re.split(r"\n\s*\n", answer):
        para = paragraph.strip()
        if not para or para.startswith(("#", "$$", "```", "|")):
            continue
        lines = [ln.strip() for ln in para.splitlines() if ln.strip()]
        for line in lines:
            line = re.sub(r"^([-*+]|\d+[.)])\s+", "", line)
            sentences = split_sentences(line)
            merged: list[str] = []
            for s in sentences:
                # A citation that starts a "sentence" belongs to the previous one.
                if merged and _BRACKET.match(s.strip()):
                    merged[-1] = f"{merged[-1]} {s}"
                else:
                    merged.append(s)
            for s in merged:
                prose = _BRACKET.sub("", s)
                prose = re.sub(r"\$[^$]*\$", " x ", prose)
                if len(prose.split()) < _MIN_CLAIM_WORDS or prose.rstrip().endswith(":"):
                    continue
                claims += 1
                if not _BRACKET.search(s):
                    uncited.append(s.strip())
    return uncited, claims
