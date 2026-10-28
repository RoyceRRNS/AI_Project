"""Shared data types passed between ingestion, retrieval and generation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

BlockType = Literal[
    "title", "heading", "text", "list_item", "formula", "table", "caption", "code", "picture"
]

ATOMIC_BLOCK_TYPES = frozenset({"formula", "table", "code"})


@dataclass(frozen=True)
class BBox:
    """A rectangle on a PDF page in points, origin at the top-left corner."""

    page: int
    x0: float
    y0: float
    x1: float
    y1: float

    def to_dict(self) -> dict[str, float]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BBox":
        return cls(
            page=int(data["page"]),
            x0=float(data["x0"]),
            y0=float(data["y0"]),
            x1=float(data["x1"]),
            y1=float(data["y1"]),
        )


@dataclass
class Block:
    """One layout element extracted from a PDF, already rendered as Markdown."""

    type: BlockType
    text: str
    page: int
    bbox: BBox | None = None
    level: int = 0

    @property
    def is_atomic(self) -> bool:
        return self.type in ATOMIC_BLOCK_TYPES

    @property
    def is_heading(self) -> bool:
        return self.type in ("title", "heading")

    def to_markdown(self) -> str:
        if self.type == "title":
            return f"# {self.text}"
        if self.type == "heading":
            return f"{'#' * max(1, min(self.level or 2, 6))} {self.text}"
        if self.type == "formula":
            body = self.text.strip()
            if body.startswith("$$"):
                return body
            return f"$$\n{body}\n$$"
        if self.type == "list_item":
            return f"- {self.text}"
        if self.type == "code":
            return f"```\n{self.text}\n```"
        return self.text

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "text": self.text,
            "page": self.page,
            "bbox": self.bbox.to_dict() if self.bbox else None,
            "level": self.level,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Block":
        return cls(
            type=data["type"],
            text=data["text"],
            page=int(data["page"]),
            bbox=BBox.from_dict(data["bbox"]) if data.get("bbox") else None,
            level=int(data.get("level", 0)),
        )


@dataclass
class ParsedDocument:
    pdf_name: str
    num_pages: int
    page_sizes: list[tuple[float, float]]
    blocks: list[Block]
    title: str | None = None
    is_slides: bool = False
    engine: str = ""

    def to_markdown(self) -> str:
        return "\n\n".join(b.to_markdown() for b in self.blocks if b.text.strip())


@dataclass
class ParentChunk:
    parent_id: str
    document_id: str
    course_id: str
    pdf_name: str
    lecture_title: str
    section_title: str
    section_path: list[str]
    blocks: list[Block]
    token_count: int
    ord: int

    @property
    def text(self) -> str:
        return "\n\n".join(b.to_markdown() for b in self.blocks)

    @property
    def page_start(self) -> int:
        return min(b.page for b in self.blocks)

    @property
    def page_end(self) -> int:
        return max(b.page for b in self.blocks)


@dataclass
class ChildChunk:
    chunk_id: str
    parent_id: str
    document_id: str
    course_id: str
    pdf_name: str
    section_title: str
    raw_text: str
    contextualized_text: str
    page_num: int
    page_end: int
    bboxes: list[BBox]
    token_count: int
    ord: int


@dataclass
class RetrievedChunk:
    """A parent chunk that survived reranking, with everything the UI needs to cite it."""

    parent_id: str
    document_id: str
    course_id: str
    pdf_name: str
    lecture_title: str
    section_title: str
    blocks: list[Block]
    page_start: int
    page_end: int
    rrf_score: float = 0.0
    rerank_score: float = 0.0
    matched_child_ids: list[str] = field(default_factory=list)
    highlight_bboxes: list[BBox] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n\n".join(b.to_markdown() for b in self.blocks)

    def bboxes_on_page(self, page: int) -> list[BBox]:
        matched = [b for b in self.highlight_bboxes if b.page == page]
        if matched:
            return matched
        return [b.bbox for b in self.blocks if b.bbox is not None and b.page == page]

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "parent_id": self.parent_id,
            "document_id": self.document_id,
            "pdf_name": self.pdf_name,
            "lecture_title": self.lecture_title,
            "section_title": self.section_title,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "rrf_score": round(self.rrf_score, 6),
            "rerank_score": round(self.rerank_score, 4),
            "excerpt": _excerpt(self.text),
            "highlight_bboxes": [b.to_dict() for b in self.highlight_bboxes],
        }


def _excerpt(text: str, limit: int = 320) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"
