"""Parent-child hierarchical chunking over layout blocks.

Parents follow the document structure (``#``/``##``/``###`` headings, or one slide per
parent for slide decks). Adjacent small sections of the same lecture are merged up to a
target of ~800 tokens and nothing exceeds 1200 tokens. Children are
200-300 token search units with a 50-token overlap. Formula, table and code blocks are
atomic: no parent or child boundary ever falls inside one.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from course_notes_rag.models import BBox, Block, ChildChunk, ParentChunk, ParsedDocument
from course_notes_rag.text_utils import (
    TokenCounter, approx_token_count, linearize_tables_in, split_sentences, tail_words,
)

SPLIT_HEADING_LEVEL = 3
_NEW_LECTURE = re.compile(r"^(lecture|chapter|week|unit|module|part)\s+\d+", re.IGNORECASE)


@dataclass
class ChunkingResult:
    parents: list[ParentChunk]
    children: list[ChildChunk]


@dataclass
class _Section:
    title: str
    path: list[str]
    blocks: list[Block] = field(default_factory=list)
    tokens: int = 0

    @property
    def has_content(self) -> bool:
        return any(not b.is_heading for b in self.blocks)


@dataclass
class _Unit:
    """A child-level packing unit: a sentence, or a whole atomic block."""

    text: str
    tokens: int
    page: int
    bbox: BBox | None
    atomic: bool
    block_index: int
    section_title: str


def _starts_lecture(section: _Section) -> bool:
    """Layout models often report every heading as level 1, so only an explicit "Lecture N" /
    "Chapter N" heading marks a boundary that merging must not cross."""
    first = section.blocks[0] if section.blocks else None
    return bool(first and first.is_heading and _NEW_LECTURE.match(first.text.strip()))


def _fold(pending: _Section, section: _Section) -> _Section:
    """Merge a small section into the next one; a heading-only section adopts the next title."""
    if not pending.has_content:
        return _Section(title=section.title, path=section.path,
                        blocks=pending.blocks + section.blocks,
                        tokens=pending.tokens + section.tokens)
    return _combine([pending, section])


def _combine(group: list[_Section]) -> _Section:
    common: list[str] = []
    for parts in zip(*(g.path for g in group)):
        if len(set(parts)) != 1:
            break
        common.append(parts[0])
    if len(common) >= 2:
        title, path = common[-1], common
    else:
        title = f"{group[0].title.split(' – ')[0]} – {group[-1].title.split(' – ')[-1]}"
        path = [*common, title] if common else [title]
    return _Section(
        title=title, path=path, blocks=[b for g in group for b in g.blocks],
        tokens=sum(g.tokens for g in group),
    )


def contextual_prefix(course_name: str, lecture_title: str, section_title: str) -> str:
    lecture = lecture_title.strip()
    if not lecture.lower().startswith("lecture"):
        lecture = f"Lecture: {lecture}"
    return f"[Course: {course_name.strip()} | {lecture} | Section: {section_title.strip()}] "


class HierarchicalChunker:
    def __init__(
        self,
        count_tokens: TokenCounter = approx_token_count,
        *,
        parent_min_tokens: int = 200,
        parent_target_tokens: int = 800,
        parent_max_tokens: int = 1200,
        child_min_tokens: int = 200,
        child_max_tokens: int = 300,
        child_overlap_tokens: int = 50,
    ) -> None:
        if not (0 < parent_min_tokens <= parent_target_tokens <= parent_max_tokens):
            raise ValueError("Invalid parent token bounds")
        if not (0 < child_min_tokens <= child_max_tokens):
            raise ValueError("Invalid child token bounds")
        if not (0 <= child_overlap_tokens < child_min_tokens):
            raise ValueError("child_overlap_tokens must be smaller than child_min_tokens")
        self.count = count_tokens
        self.parent_min = parent_min_tokens
        self.parent_target = parent_target_tokens
        self.parent_max = parent_max_tokens
        self.child_min = child_min_tokens
        self.child_max = child_max_tokens
        self.overlap = child_overlap_tokens

    # --- Public API -------------------------------------------------------------

    def chunk(
        self, doc: ParsedDocument, *, document_id: str, course_id: str, course_name: str
    ) -> ChunkingResult:
        blocks = [b for b in doc.blocks if b.text.strip()]
        if not blocks:
            return ChunkingResult([], [])
        lecture_title = self._lecture_title(doc, blocks)
        sections = self._sections(blocks, lecture_title, split_on_pages=doc.is_slides)
        sections = self._split_oversized(sections)
        if not doc.is_slides:
            sections = self._merge_undersized(sections)
            sections = self._merge_to_target(sections)

        parents: list[ParentChunk] = []
        children: list[ChildChunk] = []
        for p_ord, section in enumerate(sections):
            parent = ParentChunk(
                parent_id=str(uuid.uuid4()),
                document_id=document_id,
                course_id=course_id,
                pdf_name=doc.pdf_name,
                lecture_title=lecture_title,
                section_title=section.title,
                section_path=section.path,
                blocks=section.blocks,
                token_count=self.count(
                    "\n\n".join(b.to_markdown() for b in section.blocks)
                ),
                ord=p_ord,
            )
            parents.append(parent)
            children.extend(
                self._children(parent, course_name, block_sections=self._block_titles(section))
            )
        for c_ord, child in enumerate(children):
            child.ord = c_ord
        return ChunkingResult(parents, children)

    # --- Parents ----------------------------------------------------------------

    @staticmethod
    def _lecture_title(doc: ParsedDocument, blocks: list[Block]) -> str:
        if doc.title and doc.title.strip():
            return doc.title.strip()
        for b in blocks:
            if b.type == "title" or (b.type == "heading" and b.level == 1):
                return b.text.strip()
        return Path(doc.pdf_name).stem.replace("_", " ").strip() or doc.pdf_name

    def _sections(
        self, blocks: list[Block], lecture_title: str, *, split_on_pages: bool
    ) -> list[_Section]:
        sections: list[_Section] = []
        stack: list[tuple[int, str]] = []
        current = _Section(title=lecture_title, path=[lecture_title])
        current_page = blocks[0].page

        def flush() -> None:
            nonlocal current
            if current.blocks:
                sections.append(current)
            title = stack[-1][1] if stack else lecture_title
            current = _Section(title=title, path=[t for _, t in stack] or [lecture_title])

        for block in blocks:
            level = 1 if block.type == "title" else block.level
            if block.is_heading and level <= SPLIT_HEADING_LEVEL:
                if current.blocks:
                    flush()
                while stack and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, block.text.strip()))
                current = _Section(title=block.text.strip(), path=[t for _, t in stack])
            elif split_on_pages and block.page != current_page and current.has_content:
                flush()
            current_page = block.page
            current.blocks.append(block)
            current.tokens += self.count(block.to_markdown())
        flush()
        return sections

    def _split_oversized(self, sections: list[_Section]) -> list[_Section]:
        out: list[_Section] = []
        for section in sections:
            if section.tokens <= self.parent_max:
                out.append(section)
                continue
            parts: list[_Section] = []
            part = _Section(title=section.title, path=section.path)
            for block in self._explode_long_text(section.blocks, self.parent_max):
                tokens = self.count(block.to_markdown())
                if part.blocks and part.tokens + tokens > self.parent_max:
                    parts.append(part)
                    part = _Section(title=section.title, path=section.path)
                part.blocks.append(block)
                part.tokens += tokens
            if part.blocks:
                parts.append(part)
            if len(parts) > 1:
                for i, p in enumerate(parts[1:], start=2):
                    p.title = f"{section.title} (part {i})"
            out.extend(parts)
        return out

    def _merge_undersized(self, sections: list[_Section]) -> list[_Section]:
        """Fold tiny sections (often a lone heading) into the following section."""
        merged: list[_Section] = []
        pending: _Section | None = None

        def close_pending() -> None:
            """Attach a tiny section that cannot move forward to the previous one."""
            nonlocal pending
            if pending is None:
                return
            if merged and merged[-1].tokens + pending.tokens <= self.parent_max:
                last = merged.pop()
                if pending.has_content:
                    merged.append(_combine([last, pending]))
                else:  # a trailing lone heading just joins the previous section
                    last.blocks = last.blocks + pending.blocks
                    last.tokens += pending.tokens
                    merged.append(last)
            else:
                merged.append(pending)
            pending = None

        for section in sections:
            if pending is not None:
                if _starts_lecture(section):
                    close_pending()
                elif pending.tokens + section.tokens <= self.parent_max:
                    section = _fold(pending, section)
                    pending = None
                else:
                    merged.append(pending)
                    pending = None
            if section.tokens < self.parent_min:
                pending = section
            else:
                merged.append(section)
        close_pending()
        return merged

    def _merge_to_target(self, sections: list[_Section]) -> list[_Section]:
        """Greedily join neighbouring sections of the same lecture until ~parent_target tokens."""
        merged: list[_Section] = []
        group: list[_Section] = []

        def flush() -> None:
            if not group:
                return
            if len(group) == 1:
                merged.append(group[0])
            else:
                merged.append(_combine(group))
            group.clear()

        for section in sections:
            if group:
                tokens = sum(g.tokens for g in group)
                if (_starts_lecture(section) or tokens >= self.parent_target
                        or tokens + section.tokens > self.parent_max):
                    flush()
            group.append(section)
        flush()
        return merged

    def _explode_long_text(self, blocks: list[Block], limit: int) -> list[Block]:
        """Split non-atomic blocks longer than ``limit`` tokens into sentence groups."""
        out: list[Block] = []
        for block in blocks:
            if block.is_atomic or self.count(block.to_markdown()) <= limit:
                out.append(block)
                continue
            group: list[str] = []
            for sentence in split_sentences(block.text):
                candidate = " ".join(group + [sentence])
                if group and self.count(candidate) > limit:
                    out.append(Block(block.type, " ".join(group), block.page, block.bbox, block.level))
                    group = [sentence]
                else:
                    group.append(sentence)
            if group:
                out.append(Block(block.type, " ".join(group), block.page, block.bbox, block.level))
        return out

    @staticmethod
    def _block_titles(section: _Section) -> list[str]:
        """Section title in force at each block (deeper headings inside a parent count)."""
        titles: list[str] = []
        current = section.title
        for block in section.blocks:
            if block.is_heading:
                current = block.text.strip()
            titles.append(current)
        return titles

    # --- Children ---------------------------------------------------------------

    def _units(self, parent: ParentChunk, block_titles: list[str]) -> list[_Unit]:
        units: list[_Unit] = []
        heading_prefix: list[str] = []
        for idx, block in enumerate(parent.blocks):
            if block.is_heading:
                heading_prefix.append(block.to_markdown())
                continue
            if block.is_atomic:
                pieces = [block.to_markdown()]
            else:
                pieces = split_sentences(block.text) or [block.text]
                if block.type == "list_item":
                    pieces = [f"- {pieces[0]}", *pieces[1:]]
            for n, piece in enumerate(pieces):
                text = piece
                if n == 0 and heading_prefix:
                    text = "\n\n".join([*heading_prefix, piece])
                    heading_prefix = []
                units.append(
                    _Unit(
                        text=text,
                        tokens=self.count(text),
                        page=block.page,
                        bbox=block.bbox,
                        atomic=block.is_atomic,
                        block_index=idx,
                        section_title=block_titles[idx],
                    )
                )
        if heading_prefix:
            # A parent made only of headings still becomes one searchable child.
            last = parent.blocks[-1]
            text = "\n\n".join(heading_prefix)
            units.append(
                _Unit(text, self.count(text), last.page, last.bbox, False, len(parent.blocks) - 1,
                      block_titles[-1])
            )
        return units

    def _children(
        self, parent: ParentChunk, course_name: str, *, block_sections: list[str]
    ) -> list[ChildChunk]:
        units = self._units(parent, block_sections)
        children: list[ChildChunk] = []
        start = 0
        overlap_text = ""
        while start < len(units):
            window: list[_Unit] = []
            tokens = self.count(overlap_text) if overlap_text else 0
            end = start
            while end < len(units):
                unit = units[end]
                if window and tokens + unit.tokens > self.child_max:
                    break
                window.append(unit)
                tokens += unit.tokens
                end += 1
            children.append(self._make_child(parent, course_name, window, overlap_text))
            if end >= len(units):
                break
            overlap_text = self._overlap_from(window)
            start = end
        return children

    def _overlap_from(self, window: list[_Unit]) -> str:
        """The last ~``overlap`` tokens of a window, never cutting into an atomic block."""
        if self.overlap == 0:
            return ""
        collected: list[str] = []
        budget = self.overlap
        for unit in reversed(window):
            if unit.tokens <= budget:
                collected.insert(0, unit.text)
                budget -= unit.tokens
                continue
            if not unit.atomic:  # never cut into a formula, table or code block
                tail = tail_words(unit.text, budget, self.count)
                if tail:
                    collected.insert(0, tail)
            break
        return " ".join(collected)

    def _make_child(
        self, parent: ParentChunk, course_name: str, window: list[_Unit], overlap_text: str
    ) -> ChildChunk:
        parts: list[str] = [overlap_text] if overlap_text else []
        prev_block: int | None = None
        for unit in window:
            if parts and prev_block is not None and unit.block_index == prev_block and not unit.atomic:
                parts[-1] = f"{parts[-1]} {unit.text}"
            else:
                parts.append(unit.text)
            prev_block = unit.block_index
        raw_text = "\n\n".join(parts)
        section_title = window[0].section_title
        bboxes: list[BBox] = []
        for unit in window:
            if unit.bbox is not None and unit.bbox not in bboxes:
                bboxes.append(unit.bbox)
        return ChildChunk(
            chunk_id=str(uuid.uuid4()),
            parent_id=parent.parent_id,
            document_id=parent.document_id,
            course_id=parent.course_id,
            pdf_name=parent.pdf_name,
            section_title=section_title,
            raw_text=raw_text,
            # Embedded text: tables are linearized into row sentences; raw_text keeps the Markdown.
            contextualized_text=contextual_prefix(course_name, parent.lecture_title, section_title)
            + linearize_tables_in(raw_text),
            page_num=min(u.page for u in window),
            page_end=max(u.page for u in window),
            bboxes=bboxes,
            token_count=self.count(raw_text),
            ord=0,
        )
