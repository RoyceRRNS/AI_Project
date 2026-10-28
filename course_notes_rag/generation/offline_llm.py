"""A deterministic, extractive stand-in for the LLM.

Used by the ``test`` profile so the whole product (API and UI) can be exercised end to
end without a GPU or Ollama. It follows the same contracts as the real model: grounded
answers with ``[Source: file, p. N]`` citations, and schema-valid quiz JSON whose source
quote is copied word for word from the notes.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from typing import Any

from course_notes_rag.generation.evidence import parse_numbered_notes
from course_notes_rag.generation.prompts import NOT_FOUND_MESSAGE
from course_notes_rag.retrieval.lexical import tokenize
from course_notes_rag.text_utils import split_sentences

_SOURCE = re.compile(r'<source id="\d+" pdf="(?P<pdf>[^"]+)"[^>]*>\n(?P<body>.*?)\n</source>', re.S)
_NOTES = re.compile(r'<notes pdf="(?P<pdf>[^"]+)" section="(?P<section>[^"]*)">\n(?P<body>.*?)\n</notes>', re.S)
_PAGE = re.compile(r"^\[p\. (\d+)\]$")

_SIGN_SWAPS = [
    ("increases", "decreases"), ("decreases", "increases"), ("larger", "smaller"),
    ("smaller", "larger"), ("positive", "negative"), ("negative", "positive"),
    ("maximize", "minimize"), ("minimize", "maximize"), ("maximizes", "minimizes"),
    ("minimizes", "maximizes"), ("greater", "less"), ("less", "greater"), ("+", "-"),
    ("above", "below"), ("below", "above"), ("high", "low"), ("low", "high"),
]


def _sentences_with_pages(body: str) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    page = 1
    for line in body.splitlines():
        line = line.strip()
        m = _PAGE.match(line)
        if m:
            page = int(m.group(1))
            continue
        if not line or line.startswith(("#", "|", "$$", "```")) or line.endswith("$$"):
            continue
        line = re.sub(r"^- ", "", line)
        for sentence in split_sentences(line):
            if len(sentence.split()) >= 6 and "$" not in sentence:
                out.append((sentence, page))
    return out


def _flip(sentence: str) -> str:
    for a, b in _SIGN_SWAPS:
        pattern = rf"\b{re.escape(a)}\b" if a.isalpha() else re.escape(a)
        if re.search(pattern, sentence, re.IGNORECASE):
            return re.sub(pattern, b, sentence, count=1, flags=re.IGNORECASE)
    return f"It is not the case that {sentence[0].lower()}{sentence[1:]}"


def _stable_int(text: str) -> int:
    return int(hashlib.md5(text.encode("utf-8")).hexdigest()[:8], 16)


class ExtractiveLLM:
    model = "offline-extractive"

    def __init__(self, healthy: bool = True) -> None:
        self.healthy = healthy

    # -- protocol --

    def chat(self, messages, *, temperature=None, json_schema: dict[str, Any] | None = None) -> str:
        system = messages[0]["content"] if messages else ""
        user = messages[-1]["content"] if messages else ""
        if json_schema is not None:
            return self._quiz(messages, json_schema)
        if system.startswith("Rewrite the student's latest message"):
            m = re.search(r"Latest message: (.*)$", user, re.S)
            return m.group(1).strip() if m else user
        if "different search queries" in system:
            terms = [t for t in tokenize(user) if len(t) > 2]
            return "\n".join([" ".join(terms), " ".join(reversed(terms))])
        if system.startswith("Write a short passage"):
            return user
        return self._answer(user)

    def stream_chat(self, messages, *, temperature=None) -> Iterator[str]:
        text = self._answer(messages[-1]["content"])
        for i in range(0, len(text), 16):
            yield text[i : i + 16]

    def health(self) -> dict[str, Any]:
        return {"ok": self.healthy, "provider": "offline", "model": self.model,
                "detail": "ready" if self.healthy else "down"}

    # -- behaviours --

    def _answer(self, user: str) -> str:
        question = user.rsplit("Question:", 1)[-1].split("\n\nReminder:", 1)[0].strip()
        q_terms = set(tokenize(question))
        scored: list[tuple[float, str, str, int]] = []
        for m in _SOURCE.finditer(user):
            for sentence, page in _sentences_with_pages(m.group("body")):
                overlap = len(q_terms & set(tokenize(sentence)))
                if overlap:
                    scored.append((overlap, sentence, m.group("pdf"), page))
        if not scored:
            return NOT_FOUND_MESSAGE
        scored.sort(key=lambda s: -s[0])
        lines = []
        for _, sentence, pdf, page in scored[:3]:
            claim = sentence.rstrip(".")
            lines.append(f"{claim} [Source: {pdf}, p. {page}].")
        return "Based on your notes:\n\n" + "\n\n".join(lines)

    def _quiz(self, messages, schema: dict[str, Any]) -> str:
        level = schema["properties"]["bloom_level"]["enum"][0]
        user = next(m["content"] for m in messages if m["role"] == "user")
        m = _NOTES.search(user)
        if not m:
            return "{}"
        section = m.group("section")
        candidates = [
            (sid, text) for sid, text, _page in parse_numbered_notes(m.group("body"))
            if len(text.split()) >= 6 and "$" not in text and not text.startswith(("|", "-"))
        ]
        if not candidates:
            return "{}"
        avoid = user.split("Do not repeat these questions", 1)[-1] if "Do not repeat" in user else ""
        sid, sentence = candidates[_stable_int(section + level + avoid) % len(candidates)]
        others = [s for _, s in candidates if s != sentence]
        swap_source = others[0] if others else f"The opposite of: {sentence}"
        options = [
            {"text": sentence, "is_correct": True, "trap_type": "correct",
             "misconception_explanation": "This statement is taken directly from the notes."},
            {"text": _flip(sentence), "is_correct": False, "trap_type": "sign_inversion",
             "misconception_explanation": "Inverts the direction or sign stated in the notes."},
            {"text": f"{sentence.rstrip('.')}, even when the stated assumptions do not hold.",
             "is_correct": False, "trap_type": "boundary_violation",
             "misconception_explanation": "Applies the result outside the conditions it requires."},
            {"text": swap_source if swap_source != sentence else f"Not: {sentence}",
             "is_correct": False, "trap_type": "terminology_swap",
             "misconception_explanation": "Confuses this idea with a related concept from the same lecture."},
        ]
        stem_by_level = {
            "Recall": f"Which statement about {section} matches the lecture notes?",
            "Conceptual": f"Which statement best explains the key idea of {section}?",
            "Application": f"When applying {section} to a new problem, which statement holds?",
        }
        return json.dumps({
            "bloom_level": level,
            "question_stem": stem_by_level[level],
            "options": options,
            "hint": f"Re-read the part of the notes on {section}.",
            "evidence_ids": [sid],
        })
