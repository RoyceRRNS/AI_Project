"""Reads a parsed question paper into structured questions.

Works on the blocks the shared parser produces (Docling or PyMuPDF + RapidOCR), called with
``keep_markers=True`` so "1." and "(a)" survive. Papers come in two layouts and both are handled:
numbered lines of text, and a table with Q.No / Question / Marks columns.

Each stored question is a leaf: the unit a student actually answers. A stem shared by (a) and (b)
becomes their ``context``. Alternatives joined by OR share a ``choice_group``, so they are not
counted twice when the marks of a paper are added up.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from course_notes_rag.generation.llm import LLMClient, LLMError
from course_notes_rag.models import Block, ParsedDocument
from course_notes_rag.papers import prompts
from course_notes_rag.retrieval.lexical import tokenize
from course_notes_rag.text_utils import linearize_table

log = logging.getLogger(__name__)

MAX_QUESTION_NUMBER = 60
MAX_MARKS = 100.0
# Fewer questions than this means the rules did not understand the layout.
MIN_RULE_QUESTIONS = 3
# Share of an LLM-extracted question's terms that must exist in the paper.
SOURCE_MIN_COVERAGE = 0.85
LLM_PAPER_CHARS = 16000

_PART = re.compile(
    r"^(?:PART|SECTION)\s*[-–—:.]?\s*([A-E]|I{1,3}|IV|V|[1-5])\b[\s:.\-–—]*(.*)$", re.IGNORECASE
)
_QNUM = re.compile(r"^(\d{1,2})\s*[.):]\s*(?=[A-Za-z(\[$\"'“])")
_QNUM_ALONE = re.compile(r"^(\d{1,2})\s*[.):]\s*$")
_QNUM_PREFIXED = re.compile(
    r"^Q(?:uestion)?\s*\.?\s*(?:No\.?\s*)?(\d{1,2})\s*[.):\-–]?\s*(?=\S|$)", re.IGNORECASE
)
_SUB_ALPHA = re.compile(r"^(?:\(\s*([a-h])\s*\)|([a-hA-H])[.)])\s*(?=\S|$)")
_ROMAN = r"i{1,3}|iv|v|vi{1,3}|ix|x"
_SUB_ROMAN = re.compile(rf"^(?:\(\s*({_ROMAN})\s*\)|({_ROMAN})[.)])\s*(?=\S|$)")
_OR_LINE = re.compile(r"^[\s(\[*_-]*or[\s)\]*_-]*$", re.IGNORECASE)
_INSTRUCTION = re.compile(
    r"^(?:\(?\s*)(answer|attempt|note\b|instructions?\b|all questions|each question|"
    r"any\s+\w+\s+questions?|choose\b|questions? \d)", re.IGNORECASE
)
_NOISE = re.compile(
    r"^(?:page\s*(?:no\.?\s*)?\d*(?:\s*(?:of|/)\s*\d+)?|\d+\s*/\s*\d+|\*+|[-_=~.·•]{3,}|"
    r"all the best.*|best (?:of luck|wishes).*|\W*end of (?:the )?(?:question )?paper.*|turn over|"
    r"p\.?t\.?o\.?|reg(?:\.|istration)?\s*no.*|register number.*|roll no.*|"
    r"name of the (?:candidate|student).*)$",
    re.IGNORECASE,
)
_NOT_A_QUESTION = re.compile(r"^(?:hours?|hrs?|marks?|mins?|minutes?|x\b|×)", re.IGNORECASE)

_SPLIT_QUESTION = re.compile(r"(?<=[\]).?!:])\s+(?=(?:Q\.?\s*)?\d{1,2}[.)]\s*[A-Z(])")
# Only after a sentence or marks end: "Compare (a) Bagging (b) Boosting" is one question.
_SPLIT_SUB = re.compile(rf"(?<=[\]).?!:])\s+(?=\((?:[a-h]|{_ROMAN})\)\s*[A-Z$])")
_SPLIT_OR = re.compile(r"\s+\(?\bOR\b\)?\s+(?=\(?[a-h]\)|\d{1,2}[.)]|Q\.?\s*\d)")

# Upper case only, and a bare "L2" stays: that is a norm, not a Bloom tag.
_TAG_TAIL = re.compile(
    r"(?<![\w)])(?:[\[(]\s*(?:CO\s*-?\s*(?P<co_n>\d{1,2})|(?:BL|K|L)\s*-?\s*(?P<bl>[1-6]))\s*[\])]"
    r"|CO\s*-?\s*(?P<co_bare>\d{1,2})|(?:BL\s*-?\s*|K)(?P<bl_bare>[1-6]))\s*[,;/]?\s*$"
)
_MARKS_TAIL = re.compile(
    r"(?<![\w)])[\[(]\s*(\d{1,3}(?:\.\d)?)\s*(?:marks?|mks?|m)?\.?\s*[\])]\s*$", re.IGNORECASE
)
_MARKS_WORD_TAIL = re.compile(r"[\s\-–—:]+(\d{1,3}(?:\.\d)?)\s*(?:marks?|mks?)\.?\s*$", re.IGNORECASE)
_MARKS_SUM_TAIL = re.compile(
    r"(?<![\w)])[\[(]\s*(\d{1,2}(?:\s*\+\s*\d{1,2})+)\s*(?:marks?)?\s*[\])]\s*$", re.IGNORECASE
)
_BARE_TAIL = re.compile(r"\s(\d{1,2})$")

_TIMES = re.compile(r"(\d{1,2})\s*[x×X*]\s*(\d{1,2}(?:\.\d)?)\s*(?:=\s*(\d{1,3}))?")
_EACH = re.compile(r"each\s+(?:question\s+)?carr(?:y|ies)\s+(\d{1,2}(?:\.\d)?)\s*marks?", re.IGNORECASE)
_ANY = re.compile(r"\bany\s+(\w+)\b", re.IGNORECASE)
_PART_TOTAL = re.compile(r"(\d{1,3})\s*marks?\b", re.IGNORECASE)
_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "twelve": 12, "fifteen": 15, "twenty": 20,
}

# Matched against the header cell with everything but letters and digits removed.
_HEADERS: dict[str, re.Pattern[str]] = {
    "number": re.compile(r"^(?:q|qn|qno|qns|qnum|qnumber|questionno|questionnumber|sno|slno|no)$"),
    "sub": re.compile(r"^(?:sub|subdiv\w*|subsec\w*|subq\w*|subpart|part)$"),
    "text": re.compile(r"^(?:questions?|questiondescription|questiontext|description|problem)$"),
    "marks": re.compile(r"^(?:maxmarks?|marks?|mks?|m)$"),
    "co": re.compile(r"^(?:cos?|courseoutcomes?)$"),
    "bl": re.compile(r"^(?:bl|blooms?|bloomslevels?|bloomlevels?|klevels?|kl|levels?|rbt)$"),
}
_CELL_NUMBER = re.compile(r"^Q?\.?\s*(\d{1,2})\s*[.)]?\s*(?:\(?([a-h]|" + _ROMAN + r")\)?\.?)?$", re.I)
_CELL_SUB = re.compile(r"^\(?([a-h]|" + _ROMAN + r")\)?[.)]?$", re.I)
_NUMBERISH = re.compile(r"^\d{1,3}(?:\.\d)?$")

_EXAM_TYPES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bfinal assessment\b|\bfat\b", re.I), "FAT"),
    (re.compile(r"(?:\bcontinuous assessment(?: test)?|\bcat)\s*[-–—]?\s*(?:ii|2)\b", re.I), "CAT-2"),
    (re.compile(r"(?:\bcontinuous assessment(?: test)?|\bcat)\s*[-–—]?\s*(?:i|1)\b", re.I), "CAT-1"),
    (re.compile(r"\bend[\s_-]*sem(?:ester)?\b|\bsemester end\b|\bfinal exam", re.I), "End-Semester"),
    (re.compile(r"\bmid[\s_-]*(?:sem(?:ester)?|term)\b", re.I), "Mid-Semester"),
    (re.compile(r"\bmodel (?:question )?paper\b", re.I), "Model paper"),
    (re.compile(r"\bsupplementary\b|\barrears?\b|\bre[\s-]?exam", re.I), "Supplementary"),
    (re.compile(r"\bquiz\b", re.I), "Quiz"),
    (re.compile(r"\bcat\b|\bcontinuous assessment\b", re.I), "CAT"),
]
_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_TERM = re.compile(
    r"\b((?:winter|fall|summer|spring|monsoon|autumn|odd|even)\s*(?:semester|sem\.?)?\s*[-:,]?\s*"
    r"(?:19|20)\d{2}(?:\s*[-–/]\s*\d{2,4})?|"
    rf"{_MONTH}\s*(?:[/&\-–]\s*{_MONTH}\s*)?[-,]?\s*(?:19|20)\d{{2}})\b",
    re.IGNORECASE,
)
_DATE_YEAR = re.compile(r"\b\d{1,2}[./-]\d{1,2}[./-]((?:19|20)\d{2})\b")
_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_MAX_MARKS = re.compile(
    r"(?:max(?:imum)?\.?\s*marks?|total\s*marks?)\s*[:\-–=]?\s*(\d{2,3})|\bmarks\s*[:\-–=]\s*(\d{2,3})",
    re.I,
)
_DURATION = re.compile(
    r"(?:duration|time(?:\s*allowed)?)\s*[:\-–=]?\s*"
    r"([\d½.]+\s*(?:hours?|hrs?\.?|minutes?|mins?\.?))|"
    r"\b([\d½.]+\s*(?:hours?|hrs\.?))\b",
    re.IGNORECASE,
)


@dataclass
class ExtractedQuestion:
    number: str
    text: str = ""
    sub: str = ""  # "a", "a.ii" or "ii"
    part: str = ""
    choice_group: str = ""
    context: str = ""
    marks: float | None = None
    marks_estimated: bool = False
    page: int = 1
    course_outcome: str = ""
    bloom_hint: str = ""  # the level printed on the paper, "1" to "6"

    @property
    def label(self) -> str:
        return self.number + "".join(f"({piece})" for piece in self.sub.split(".") if piece)

    @property
    def alternative(self) -> str:
        """What sets one side of an OR apart: the top-level (a) / (b), or the question number."""
        return self.sub.split(".")[0] or self.number

    def record(self) -> dict[str, Any]:
        return {
            "number": self.number, "sub": self.sub, "label": self.label, "part": self.part,
            "choice_group": self.choice_group, "context": self.context, "text": self.text,
            "marks": self.marks, "marks_estimated": int(self.marks_estimated), "page": self.page,
            "course_outcome": self.course_outcome, "bloom_hint": self.bloom_hint,
        }


@dataclass
class PaperPart:
    name: str
    instructions: str = ""
    answer_count: int | None = None
    each_marks: float | None = None
    stated_total: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "instructions": self.instructions,
            "answer_count": self.answer_count, "each_marks": self.each_marks,
            "stated_total": self.stated_total,
        }


@dataclass
class ExtractedPaper:
    title: str | None = None
    exam_type: str | None = None
    year: int | None = None
    term: str | None = None
    max_marks: float | None = None
    duration: str | None = None
    parts: list[PaperPart] = field(default_factory=list)
    questions: list[ExtractedQuestion] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# --- Lines ------------------------------------------------------------------------------


@dataclass
class _Line:
    text: str
    page: int
    # Set for a row of a question table: the columns are already known.
    number: str | None = None
    sub: str | None = None
    marks: float | None = None
    co: str = ""
    bl: str = ""
    is_row: bool = False


def split_inline(text: str) -> list[str]:
    """Separate questions, sub-questions and OR that a parser joined into one paragraph."""
    text = _SPLIT_OR.sub("\nOR\n", text)
    pieces: list[str] = []
    for chunk in text.split("\n"):
        for part in _SPLIT_QUESTION.split(chunk):
            pieces.extend(_SPLIT_SUB.split(part))
    return [" ".join(p.split()) for p in pieces if p.strip()]


def _table_rows(markdown: str) -> list[list[str]]:
    rows = [r.strip() for r in markdown.splitlines() if r.strip().startswith("|")]
    cells = [[c.strip() for c in r.strip("|").split("|")] for r in rows]
    return [r for r in cells if not all(set(c) <= set("-: ") for c in r)]


def _column_roles(header: list[str]) -> dict[str, int]:
    roles: dict[str, int] = {}
    for i, cell in enumerate(header):
        name = re.sub(r"[^a-z0-9]", "", cell.lower())
        for role, pattern in _HEADERS.items():
            if role not in roles and pattern.match(name):
                roles[role] = i
                break
    return roles


def _inferred_roles(rows: list[list[str]]) -> dict[str, int]:
    """A question table with no usable header: the longest column is the question."""
    width = max(len(r) for r in rows)
    if width < 2:
        return {}
    lengths = [sum(len(r[i]) for r in rows if i < len(r)) for i in range(width)]
    text = lengths.index(max(lengths))
    numbered = [r for r in rows if r and _CELL_NUMBER.match(r[0])]
    if text == 0 or len(numbered) < max(2, len(rows) // 2):
        return {}
    roles = {"number": 0, "text": text}
    for i in range(width - 1, text, -1):
        filled = [r[i] for r in rows if i < len(r) and r[i]]
        if filled and all(_NUMBERISH.match(c) for c in filled):
            roles["marks"] = i
            break
    return roles


def _table_lines(block: Block) -> list[_Line]:
    rows = _table_rows(block.text)
    if not rows:
        return []
    roles = _column_roles(rows[0])
    body = rows[1:]
    if "text" not in roles or not ("number" in roles or "marks" in roles):
        roles = _inferred_roles(rows)
        body = rows
    if "text" not in roles:  # a data table or the exam's header box, not the questions themselves
        return [_Line(ln, block.page) for ln in linearize_table(block.text).splitlines() if ln.strip()]

    def cell(row: list[str], role: str) -> str:
        i = roles.get(role)
        return row[i].strip() if i is not None and i < len(row) else ""

    lines: list[_Line] = []
    for row in body:
        text = cell(row, "text")
        joined = " ".join(c for c in row if c).strip()
        if _OR_LINE.match(text or joined):
            lines.append(_Line("OR", block.page))
            continue
        if _PART.match(joined) and len(joined.split()) <= 14:
            lines.append(_Line(joined, block.page))
            continue
        if not text:
            continue
        number, sub = cell(row, "number"), cell(row, "sub")
        found = _CELL_NUMBER.match(number)
        marks_cell = cell(row, "marks")
        marks = float(marks_cell) if _NUMBERISH.match(marks_cell) else None
        lines.append(_Line(
            text, block.page, number=found.group(1) if found else "",
            sub=(found.group(2) if found and found.group(2) else sub.strip("(). ")).lower(),
            marks=marks, co=_digits(cell(row, "co")), bl=_digits(cell(row, "bl")), is_row=True,
        ))
    return lines


def _digits(text: str) -> str:
    match = re.search(r"\d+", text)
    return match.group(0) if match else ""


def paper_lines(doc: ParsedDocument) -> list[_Line]:
    lines: list[_Line] = []
    for block in doc.blocks:
        if block.type == "table":
            lines.extend(_table_lines(block))
        elif block.type == "formula":
            body = block.text.strip().strip("$").strip()
            if body:
                lines.append(_Line(f"${body}$", block.page))
        elif block.type != "picture":
            for raw in block.text.split("\n"):
                for piece in split_inline(raw):
                    if not _NOISE.match(piece):
                        lines.append(_Line(piece, block.page))
    return lines


# --- Marks ------------------------------------------------------------------------------


def take_trailing_tags(text: str) -> tuple[str, float | None, str, str]:
    """Strip the marks, CO and Bloom tags that end a question; returns (text, marks, co, bl)."""
    marks: float | None = None
    co = bl = ""
    text = text.rstrip()
    while True:
        tag = _TAG_TAIL.search(text)
        if tag:
            co = co or tag.group("co_n") or tag.group("co_bare") or ""
            bl = bl or tag.group("bl") or tag.group("bl_bare") or ""
            text = text[: tag.start()].rstrip()
            continue
        if marks is not None:
            return text, marks, co, bl
        summed = _MARKS_SUM_TAIL.search(text)
        if summed:
            total = float(sum(int(n) for n in summed.group(1).split("+")))
            return text[: summed.start()].rstrip(), total, co, bl
        found = _MARKS_TAIL.search(text) or _MARKS_WORD_TAIL.search(text)
        # "Explain X. 10 CO2 K2": a bare number counts as marks only beside its tags.
        found = found or (_BARE_TAIL.search(text) if co or bl else None)
        if found and 0 < float(found.group(1)) <= MAX_MARKS:
            marks = float(found.group(1))
            text = text[: found.start()].rstrip()
            continue
        return text, marks, co, bl


def parse_part_header(name: str, rest: str) -> PaperPart:
    part = PaperPart(name.upper())
    apply_instruction(part, rest)
    return part


def apply_instruction(part: PaperPart, text: str) -> None:
    text = " ".join(text.split()).strip(" -–—:")
    if not text:
        return
    part.instructions = f"{part.instructions} {text}".strip()
    times = _TIMES.search(text)
    if times:
        part.each_marks = float(times.group(2))
        part.answer_count = part.answer_count or int(times.group(1))
        if times.group(3):
            part.stated_total = float(times.group(3))
    each = _EACH.search(text)
    if each:
        part.each_marks = float(each.group(1))
    count = _ANY.search(text)
    if count:
        word = count.group(1).lower()
        value = int(word) if word.isdigit() else _NUMBER_WORDS.get(word)
        if value:
            part.answer_count = value
    if part.stated_total is None and not times:
        total = _PART_TOTAL.search(text)
        if total and not each:
            part.stated_total = float(total.group(1))


# --- Segmentation -----------------------------------------------------------------------


class _Builder:
    def __init__(self) -> None:
        self.leaves: list[ExtractedQuestion] = []
        self.parts: dict[str, PaperPart] = {}
        self.header: list[_Line] = []
        self.part = ""
        self.number = ""
        self.last_number = 0
        self.alpha = ""
        self.leaf: ExtractedQuestion | None = None
        self.stem: str = ""
        self.alpha_stem: str = ""
        self.stem_marks: dict[str, float] = {}
        self.pending_or = False
        self.group: tuple[str, str] | None = None
        self.pending_instruction: list[str] = []
        self.question_in_part = False

    # -- structure --

    def start_part(self, name: str, rest: str) -> None:
        self.finish_leaf()
        self.part = name.upper()
        self.number = ""
        part = self.parts.setdefault(self.part, parse_part_header(name, ""))
        for text in self.pending_instruction:
            apply_instruction(part, text)
        self.pending_instruction = []
        apply_instruction(part, rest)
        self.question_in_part = False
        self.pending_or = False

    def _begin(self, leaf: ExtractedQuestion) -> None:
        if self.pending_or and self.leaves:
            prev = self.leaves[-1]
            key = prev.choice_group or prev.number
            for other in self.leaves:
                if other.number == prev.number and other.part == prev.part:
                    other.choice_group = key
            leaf.choice_group = key
            self.group = (key, leaf.number)
        elif self.group and leaf.number == self.group[1]:
            leaf.choice_group = self.group[0]
        else:
            self.group = None
        self.pending_or = False
        self.leaf = leaf

    def start_question(self, number: str, rest: str, line: _Line) -> None:
        self.finish_leaf()
        self.number = number
        self.last_number = int(number)
        self.alpha = ""
        self.stem = self.alpha_stem = ""
        self.question_in_part = True
        self._begin(ExtractedQuestion(number, rest, part=self.part, page=line.page))
        self._apply_row(line)

    def start_sub(self, token: str, rest: str, line: _Line) -> None:
        token = token.lower()
        is_roman = _fullmatch_roman(token) and not (token == "i" and self.alpha == "h")
        previous = self.leaf
        if previous is not None and previous.number == self.number and not previous.sub:
            # The text before the first (a) is a stem shared by the sub-questions.
            self.stem, marks = _stem_of(previous)
            if marks:
                self.stem_marks[self._stem_key()] = marks
            self.leaf = None
        elif previous is not None and is_roman and token == "i" and self.alpha == previous.sub:
            # (a) stem (i) ... (ii) ...: the text of (a) is a stem for its roman parts.
            self.alpha_stem, marks = _stem_of(previous)
            if marks:
                self.stem_marks[f"{self._stem_key()}.{self.alpha}"] = marks
            self.leaf = None
        self.finish_leaf()
        if is_roman:
            sub = f"{self.alpha}.{token}" if self.alpha else token
        else:
            self.alpha = token
            self.alpha_stem = ""
            sub = token
        context = f"{self.stem} {self.alpha_stem}".strip()
        leaf = ExtractedQuestion(
            self.number, rest, sub=sub, part=self.part, page=line.page, context=context,
        )
        self.question_in_part = True
        self._begin(leaf)
        self._apply_row(line)

    def _stem_key(self) -> str:
        return f"{self.part}/{self.number}"

    def _apply_row(self, line: _Line) -> None:
        if self.leaf is None:
            return
        if line.marks is not None:
            self.leaf.marks = line.marks
        self.leaf.course_outcome = line.co or self.leaf.course_outcome
        self.leaf.bloom_hint = line.bl or self.leaf.bloom_hint

    def append(self, text: str) -> None:
        if self.leaf is not None:
            self.leaf.text = f"{self.leaf.text} {text}".strip()

    def finish_leaf(self) -> None:
        leaf = self.leaf
        self.leaf = None
        if leaf is None:
            return
        text, marks, co, bl = take_trailing_tags(leaf.text)
        leaf.text = text.strip()
        leaf.marks = leaf.marks if leaf.marks is not None else marks
        leaf.course_outcome = leaf.course_outcome or co
        leaf.bloom_hint = leaf.bloom_hint or bl
        if len(leaf.text.split()) >= 2:
            self.leaves.append(leaf)

    # -- one line --

    def plausible_number(self, value: int) -> bool:
        if value == 0 or value > MAX_QUESTION_NUMBER:
            return False
        if self.last_number == 0 or value == 1:
            return True
        return self.last_number < value <= self.last_number + 4

    def feed(self, line: _Line) -> None:
        text = line.text.strip()
        if line.is_row:
            self.feed_row(line)
            return
        part = _PART.match(text)
        if part and len(text.split()) <= 18:
            self.start_part(part.group(1), part.group(2))
            return
        if _OR_LINE.match(text):
            self.finish_leaf()
            self.pending_or = True
            return
        if _INSTRUCTION.match(text) and len(text.split()) <= 30:
            self.instruction(text, line)
            return
        for pattern in (_QNUM_PREFIXED, _QNUM, _QNUM_ALONE):
            found = pattern.match(text)
            if found and self.plausible_number(int(found.group(1))):
                rest = text[found.end():].strip()
                if not _NOT_A_QUESTION.match(rest):
                    self.start_question(found.group(1), "", line)
                    if rest:
                        self.feed_body(rest, line)
                    return
        self.feed_body(text, line)

    def feed_body(self, text: str, line: _Line) -> None:
        if self.number:
            for pattern, group_a, group_b in ((_SUB_ALPHA, 1, 2), (_SUB_ROMAN, 1, 2)):
                found = pattern.match(text)
                if found:
                    token = found.group(group_a) or found.group(group_b)
                    self.start_sub(token, text[found.end():].strip(), line)
                    return
        if self.leaf is not None:
            self.append(text)
        elif self.number:  # text after an OR or a stem with no open leaf is ignored safely
            return
        else:
            self.header.append(line)

    def instruction(self, text: str, line: _Line) -> None:
        if self.part and not self.question_in_part:
            apply_instruction(self.parts[self.part], text)
        elif not self.number:
            self.pending_instruction.append(text)
            self.header.append(line)
        elif self.leaf is not None and "question" not in text.lower():
            self.append(text)  # "Note: assume ..." inside a question belongs to that question
        else:
            self.finish_leaf()
            self.pending_instruction.append(text)

    def feed_row(self, line: _Line) -> None:
        text = line.text
        body, marks, co, bl = take_trailing_tags(text)
        line.marks = line.marks if line.marks is not None else marks
        line.co = line.co or co
        line.bl = line.bl or bl
        if line.number and line.number != self.number:
            self.start_question(line.number, "", _Line("", line.page))
        if not self.number:
            self.header.append(_Line(body, line.page))
            return
        if line.sub:
            self.start_sub(line.sub, body, line)
            return
        marker = _SUB_ALPHA.match(body) or _SUB_ROMAN.match(body)
        if marker:
            token = marker.group(1) or marker.group(2)
            self.start_sub(token, body[marker.end():].strip(), line)
        elif self.leaf is not None:
            self.append(body)
            self._apply_row(line)


def _fullmatch_roman(token: str) -> bool:
    return re.fullmatch(_ROMAN, token) is not None


def _stem_of(leaf: ExtractedQuestion) -> tuple[str, float | None]:
    text, marks, _co, _bl = take_trailing_tags(leaf.text)
    return text, leaf.marks if leaf.marks is not None else marks


def _fill_marks(builder: _Builder) -> None:
    """Marks stated once for a group: on the part header, or on a stem shared by sub-questions."""
    if builder.pending_instruction and "" not in builder.parts:
        if any(not q.part for q in builder.leaves):
            builder.parts[""] = PaperPart("")
            for text in builder.pending_instruction:
                apply_instruction(builder.parts[""], text)
    groups: dict[tuple[str, str, str], list[ExtractedQuestion]] = {}
    for leaf in builder.leaves:
        # Sides of an OR are worth the same total each; plain sub-questions share one total.
        side = leaf.alternative if leaf.choice_group else ""
        groups.setdefault((leaf.part, leaf.number, side), []).append(leaf)
    for (part, number, _side), leaves in groups.items():
        for alpha in dict.fromkeys(q.sub.split(".")[0] for q in leaves):
            inner = builder.stem_marks.get(f"{part}/{number}.{alpha}")
            if inner and alpha:
                _share_out([q for q in leaves if q.sub.split(".")[0] == alpha], inner)
        each = builder.parts[part].each_marks if part in builder.parts else None
        total = builder.stem_marks.get(f"{part}/{number}") or each
        if total is not None:
            _share_out(leaves, total)


def _share_out(leaves: list[ExtractedQuestion], total: float) -> None:
    missing = [q for q in leaves if q.marks is None]
    if not missing:
        return
    if len(leaves) == 1:
        leaves[0].marks = total
        return
    share = max(total - sum(q.marks or 0 for q in leaves), 0) / len(missing)
    for leaf in missing:
        leaf.marks = round(share, 1) or None
        leaf.marks_estimated = True


# --- Paper metadata -----------------------------------------------------------------------


def parse_metadata(header: str, pdf_name: str, doc_title: str | None = None) -> dict[str, Any]:
    stem = Path(pdf_name).stem.replace("_", " ")
    haystacks = [header, doc_title or "", stem]
    exam_type = next(
        (name for hay in haystacks for pattern, name in _EXAM_TYPES if pattern.search(hay)), None
    )
    term_match = next((m for hay in haystacks if (m := _TERM.search(hay))), None)
    term = " ".join(term_match.group(1).split()) if term_match else None
    year: int | None = None
    for hay in haystacks:
        found = _DATE_YEAR.search(hay)
        if found:
            year = int(found.group(1))
            break
    if year is None:
        source = [term or ""] + haystacks
        found_year = next((m for hay in source if (m := _YEAR.search(hay))), None)
        year = int(found_year.group(1)) if found_year else None
    marks = _MAX_MARKS.search(header)
    duration = _DURATION.search(header)
    label = " ".join(x for x in (exam_type, term or (str(year) if year else None)) if x)
    return {
        "exam_type": exam_type,
        "year": year,
        "term": term,
        "max_marks": float(marks.group(1) or marks.group(2)) if marks else None,
        "duration": " ".join((duration.group(1) or duration.group(2)).split()) if duration else None,
        "title": label or (doc_title or "").strip() or stem,
    }


# --- Entry points ---------------------------------------------------------------------------


def extract_paper(doc: ParsedDocument) -> ExtractedPaper:
    builder = _Builder()
    for line in paper_lines(doc):
        builder.feed(line)
    builder.finish_leaf()
    _fill_marks(builder)
    header = "\n".join(line.text for line in builder.header)
    meta = parse_metadata(header, doc.pdf_name, doc.title)
    paper = ExtractedPaper(
        title=meta["title"], exam_type=meta["exam_type"], year=meta["year"], term=meta["term"],
        max_marks=meta["max_marks"], duration=meta["duration"],
        parts=list(builder.parts.values()), questions=builder.leaves,
    )
    unmarked = sum(q.marks is None for q in paper.questions)
    if unmarked:
        paper.warnings.append(
            f"{unmarked} question{'s' if unmarked != 1 else ''} had no marks printed beside them."
        )
    return paper


class _LLMQuestion(BaseModel):
    number: str
    sub: str = ""
    part: str = ""
    marks: float | None = None
    text: str = Field(min_length=3)


class _LLMPaper(BaseModel):
    questions: list[_LLMQuestion]


def source_coverage(text: str, source_terms: set[str]) -> float:
    terms = set(tokenize(text))
    return len(terms & source_terms) / len(terms) if terms else 0.0


def extract_with_llm(doc: ParsedDocument, paper: ExtractedPaper, llm: LLMClient) -> ExtractedPaper:
    """For layouts the rules cannot read. A model can invent text, so each question is kept only
    if its terms are found in the paper."""
    markdown = doc.to_markdown()
    raw = llm.chat(
        prompts.extract_messages(markdown[:LLM_PAPER_CHARS]), temperature=0.0,
        json_schema=_LLMPaper.model_json_schema(),
    )
    try:
        parsed = _LLMPaper.model_validate_json(_json_object(raw))
    except ValidationError as exc:
        raise LLMError(f"unusable paper extraction: {exc}") from exc
    source = set(tokenize(markdown))
    kept: list[ExtractedQuestion] = []
    dropped = 0
    for item in parsed.questions:
        text, marks, co, bl = take_trailing_tags(item.text)
        if source_coverage(text, source) < SOURCE_MIN_COVERAGE:
            dropped += 1
            continue
        value = item.marks if item.marks is not None else marks
        number = re.sub(r"\D", "", item.number) or str(len(kept) + 1)
        kept.append(ExtractedQuestion(
            number, text.strip(), sub=re.sub(r"[^a-z.]", "", item.sub.lower()),
            part=re.sub(r"(?i)^(part|section)\s*", "", item.part).strip().upper()[:3],
            marks=value if value and 0 < value <= MAX_MARKS else None,
            page=_page_of(doc, text), course_outcome=co, bloom_hint=bl,
        ))
    if len(kept) <= len(paper.questions):
        return paper
    paper.questions = kept
    paper.warnings.append("The layout needed the language model; each question was checked against the paper's text.")
    if dropped:
        paper.warnings.append(f"{dropped} extracted question(s) were dropped: not found in the paper.")
    return paper


def _page_of(doc: ParsedDocument, text: str) -> int:
    terms = set(tokenize(text))
    best, page = 0, 1
    for block in doc.blocks:
        score = len(terms & set(tokenize(block.text)))
        if score > best:
            best, page = score, block.page
    return page


def _json_object(raw: str) -> str:
    start, end = raw.find("{"), raw.rfind("}")
    return raw[start : end + 1] if start >= 0 and end > start else raw


def read_paper(doc: ParsedDocument, llm: LLMClient | None = None) -> ExtractedPaper:
    """Rules first; the model only when they found almost nothing."""
    paper = extract_paper(doc)
    if len(paper.questions) >= MIN_RULE_QUESTIONS or llm is None:
        return paper
    try:
        return extract_with_llm(doc, paper, llm)
    except LLMError as exc:
        log.warning("LLM paper extraction failed: %s", exc)
        return paper


__all__ = [
    "ExtractedPaper", "ExtractedQuestion", "PaperPart", "apply_instruction", "extract_paper",
    "extract_with_llm", "paper_lines", "parse_metadata", "read_paper", "source_coverage",
    "split_inline", "take_trailing_tags",
]
