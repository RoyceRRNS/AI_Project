"""Labels each question: question type, Bloom level and the section of the notes it tests.

Type and Bloom level come from the command words examiners use, which are explicit and testable.
Only questions the rules cannot settle go to the model, and a level printed on the paper always
wins. Topics come from :class:`HybridRetriever`: a question no section matches is reported as a
gap in the notes rather than forced onto the nearest section.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from course_notes_rag.generation.llm import LLMClient, LLMError
from course_notes_rag.papers import prompts
from course_notes_rag.papers.taxonomy import BLOOM_LEVELS, QUESTION_TYPES

log = logging.getLogger(__name__)

LLM_BATCH = 20
# Lower than the chat threshold: an exam question is phrased unlike the notes that teach it.
TOPIC_THRESHOLD = 0.2
MIN_CONFIDENT_SCORE = 1.5
TOPIC_QUERY_CHARS = 500

_NUMBER = re.compile(r"(?<![A-Za-z_])\d+(?:\.\d+)?")
_ALGORITHM = (
    r"algorithm|procedure|method|search|bfs|dfs|a\*|minimax|alpha[- ]beta|hill[- ]climbing|"
    r"resolution|unification|chaining|backpropagation|gradient descent|k-?means|id3|c4\.5|"
    r"decision tree|perceptron|dijkstra|smo|viterbi|apriori|simplex"
)
_QUANTITY = (
    r"value|probability|output|number|cost|distance|gradient|entropy|variance|mean|norm|margin|"
    r"weights?|information gain|accuracy|precision|recall|error|loss"
)
_CUES: list[tuple[str, float, re.Pattern[str]]] = [
    ("case_based", 3.0, re.compile(r"\bcase study\b|\bcase[- ]based\b|\bscenario\b")),
    ("case_based", 2.5, re.compile(
        r"\b(?:a|an|the)\s+(?:\w+\s+)?(?:company|hospital|bank|retailer|start-?up|firm|factory|"
        r"university|airline|insurer|city|farm|school|manufacturer)\b"
        r"[^?!]{0,160}\b(?:wants?|needs?|collects?|plans?|has|is (?:building|planning|trying))\b"
    )),
    ("derivation", 3.0, re.compile(
        r"\b(?:derive|derivation|prove|proof|deduce|show that|establish that|obtain an expression)\b"
    )),
    ("diagram", 3.0, re.compile(r"\b(?:draw|sketch)\b")),
    ("diagram", 2.5, re.compile(
        r"\b(?:neat|labell?ed|block|schematic|architecture)\s+(?:diagram|sketch|figure)\b|\bflow ?chart\b"
    )),
    ("diagram", 2.0, re.compile(r"\bwith (?:a |the )?(?:suitable )?(?:diagram|figure|sketch)\b")),
    ("numerical", 2.0, re.compile(
        rf"\b(?:calculate|compute|estimate|how many|how much|"
        rf"(?:find|determine|evaluate) the (?:\w+ )?(?:{_QUANTITY}))\b"
    )),
    ("algorithm", 3.0, re.compile(r"\btrace\b|\bsimulate\b|\bdry[- ]run\b|\bwork through\b")),
    ("algorithm", 3.0, re.compile(
        rf"\b(?:apply|use|using|perform|execute|run|construct|show the steps of)\b[^.?!]*\b(?:{_ALGORITHM})\b"
    )),
    ("explain_compare", 1.5, re.compile(
        r"\b(?:explain|describe|discuss|compare|contrast|differentiate|distinguish|elaborate|"
        r"illustrate|justify|why|how does|analy[sz]e|examine|comment on|advantages|disadvantages|"
        r"merits|demerits|significance|difference between|role of)\b"
    )),
    ("definition", 2.0, re.compile(
        r"^\s*(?:define|what (?:is|are|do you mean by)|state|list|name|mention|write (?:down|short "
        r"notes?|a note)|give the definition|expand|enumerate|outline)\b"
    )),
    ("definition", 1.2, re.compile(r"\bdefin(?:e|ition)\b")),
]
# Ties go to the type a student would have to prepare differently for.
_PRECEDENCE = ("case_based", "numerical", "algorithm", "derivation", "diagram", "explain_compare",
               "definition")

_BLOOM_VERBS: list[tuple[str, re.Pattern[str]]] = [
    ("Create", re.compile(r"\b(?:design|formulate|propose|develop|devise|create|compose|build)\b")),
    ("Evaluate", re.compile(r"\b(?:evaluate|justify|critique|assess|argue|defend|judge|recommend|critically)\b")),
    ("Analyze", re.compile(
        r"\b(?:analy[sz]e|compare|contrast|differentiate|distinguish|examine|investigate|infer|"
        r"categori[sz]e|prove|deduce)\b"
    )),
    ("Apply", re.compile(
        r"\b(?:apply|compute|calculate|solve|trace|use|demonstrate|show|find|determine|simulate|"
        r"implement|convert|perform|execute|derive|construct|draw|sketch)\b"
    )),
    ("Understand", re.compile(r"\b(?:explain|describe|discuss|summari[sz]e|interpret|classify|illustrate|why|how)\b")),
    ("Remember", re.compile(r"\b(?:define|list|state|name|what|recall|identify|mention|write|expand|label|enumerate|outline)\b")),
]
_BOILERPLATE = re.compile(
    r"\b(?:define|state|list|explain|describe|discuss|compare|contrast|differentiate|distinguish|"
    r"derive|prove|show that|show|compute|calculate|evaluate|determine|find|draw|sketch|trace|"
    r"apply|illustrate|write short notes? on|what is meant by|what is|what are|with a neat diagram|"
    r"with neat diagram|with a suitable example|with an example|briefly|in detail|neat|"
    r"step by step|justify your answer)\b",
    re.IGNORECASE,
)


def numbers_in(text: str) -> list[str]:
    return _NUMBER.findall(text)


def classify_type(text: str, marks: float | None = None) -> tuple[str, bool]:
    """Returns (question_type, confident). Not confident means the model should take a look."""
    lowered = text.lower()
    scores: dict[str, float] = {}
    for question_type, weight, pattern in _CUES:
        if pattern.search(lowered):
            scores[question_type] = max(scores.get(question_type, 0.0), weight)
    if "numerical" in scores and len(numbers_in(text)) >= 2:
        scores["numerical"] += 1.0
    if not scores:
        short = (marks is not None and marks <= 3) or len(text.split()) <= 12
        return ("definition" if short else "explain_compare"), False
    best = max(scores.values())
    top = [t for t in _PRECEDENCE if scores.get(t) == best]
    runner_up = max((s for t, s in scores.items() if s != best), default=0.0)
    confident = best >= MIN_CONFIDENT_SCORE and len(top) == 1 and best - runner_up >= 0.5
    return top[0], confident


def classify_bloom(text: str, question_type: str, printed: str = "") -> tuple[str, str]:
    """Returns (level, source). A level printed on the paper is the examiner's own label."""
    if printed.isdigit() and 1 <= int(printed) <= len(BLOOM_LEVELS):
        return BLOOM_LEVELS[int(printed) - 1], "paper"
    lowered = text.lower()
    level = next((name for name, pattern in _BLOOM_VERBS if pattern.search(lowered)), "Remember")
    rank = BLOOM_LEVELS.index(level)
    if question_type in ("numerical", "algorithm", "derivation", "case_based"):
        rank = max(rank, BLOOM_LEVELS.index("Apply"))
    if question_type == "diagram":
        rank = max(rank, BLOOM_LEVELS.index("Understand"))
    if question_type in ("numerical", "algorithm"):  # "evaluate f(2)" is arithmetic, not judgement
        rank = min(rank, BLOOM_LEVELS.index("Analyze"))
    return BLOOM_LEVELS[rank], "rules"


def topic_query(text: str, context: str = "") -> str:
    """The subject of a question: command words match every section and no section."""
    full = f"{context} {text}".strip()
    cleaned = " ".join(_BOILERPLATE.sub(" ", full).split()).strip(" .,:;?")
    return (cleaned if len(cleaned.split()) >= 2 else full)[:TOPIC_QUERY_CHARS]


class QuestionClassifier:
    def __init__(self, llm: LLMClient, retriever: Any, *, topic_threshold: float = TOPIC_THRESHOLD) -> None:
        self.llm = llm
        self.retriever = retriever
        self.topic_threshold = topic_threshold

    def classify(self, questions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """``{question_id: {question_type, type_source, bloom_level, bloom_source}}``."""
        labels: dict[str, dict[str, Any]] = {}
        unsure: list[dict[str, Any]] = []
        for q in questions:
            text = f"{q.get('context') or ''} {q['text']}".strip()
            question_type, confident = classify_type(text, q.get("marks"))
            level, source = classify_bloom(text, question_type, q.get("bloom_hint") or "")
            labels[q["id"]] = {"question_type": question_type, "type_source": "rules",
                               "bloom_level": level, "bloom_source": source}
            if not confident:
                unsure.append(q)
        for start in range(0, len(unsure), LLM_BATCH):
            self._refine(unsure[start : start + LLM_BATCH], labels)
        return labels

    def _refine(self, batch: list[dict[str, Any]], labels: dict[str, dict[str, Any]]) -> None:
        numbered = list(enumerate(batch, start=1))
        items = [(i, f"{q.get('context') or ''} {q['text']}".strip()[:600]) for i, q in numbered]
        try:
            raw = self.llm.chat(prompts.classify_messages(items), temperature=0.0,
                                json_schema=prompts.classify_schema())
            answers = json.loads(raw[raw.find("{") : raw.rfind("}") + 1]).get("items", [])
        except (LLMError, ValueError) as exc:  # the rule labels stand
            log.info("question classification kept the rule labels: %s", exc)
            return
        by_id = dict(numbered)
        for answer in answers:
            if not isinstance(answer, dict):
                continue
            q = by_id.get(answer.get("id"))
            if q is None:
                continue
            label = labels[q["id"]]
            if answer.get("question_type") in QUESTION_TYPES:
                label["question_type"] = answer["question_type"]
                label["type_source"] = "llm"
            if answer.get("bloom_level") in BLOOM_LEVELS and label["bloom_source"] != "paper":
                label["bloom_level"] = answer["bloom_level"]
                label["bloom_source"] = "llm"

    def map_topics(
        self, questions: list[dict[str, Any]], course_id: str
    ) -> dict[str, dict[str, Any] | None]:
        topics: dict[str, dict[str, Any] | None] = {}
        for q in questions:
            query = topic_query(q["text"], q.get("context") or "")
            if not query:
                topics[q["id"]] = None
                continue
            result = self.retriever.retrieve(
                query, course_id, top_n=1, threshold=self.topic_threshold, expansion="none"
            )
            if result.not_found or not result.chunks:
                topics[q["id"]] = None
                continue
            chunk = result.chunks[0]
            topics[q["id"]] = {
                "parent_id": chunk.parent_id, "section_title": chunk.section_title,
                "lecture_title": chunk.lecture_title, "notes_pdf": chunk.pdf_name,
                "document_id": chunk.document_id, "page_start": chunk.page_start,
                "page_end": chunk.page_end, "topic_score": round(chunk.rerank_score, 4),
            }
        return topics


__all__ = [
    "QUESTION_TYPES", "QuestionClassifier", "TOPIC_THRESHOLD", "classify_bloom", "classify_type",
    "numbers_in", "topic_query",
]
