"""The past-papers half of the offline stand-in for the LLM.

:class:`ExtractiveLLM` hands these prompts here, so the test profile and Playwright can run the
whole feature without a GPU. It keeps the real model's contracts: schema-valid JSON, evidence ids
that exist, and a numerical answer that matches its own expression.
"""

from __future__ import annotations

import hashlib
import json
import re

from course_notes_rag.generation.evidence import parse_numbered_notes
from course_notes_rag.papers.classify import classify_bloom, classify_type
from course_notes_rag.text_utils import split_sentences

_NOTES = re.compile(
    r'<notes pdf="[^"]+" section="(?P<section>[^"]*)" type="(?P<type>\w+)" marks="(?P<marks>[\d.]+)">'
    r"\n(?P<body>.*?)\n</notes>",
    re.S,
)
_CLASSIFY_LINE = re.compile(r"^Q(\d+): (.*)$", re.M)
_PAPER = re.compile(r"<paper>\n(.*?)\n</paper>", re.S)
_ASKS = re.compile(
    r"^(?:define|state|list|explain|describe|compare|derive|prove|compute|calculate|draw|what|why|how)\b",
    re.IGNORECASE,
)

_BLOOM_BY_TYPE = {
    "definition": "Remember", "explain_compare": "Understand", "derivation": "Apply",
    "numerical": "Apply", "algorithm": "Apply", "diagram": "Understand", "case_based": "Analyze",
}


def offline_paper_reply(messages: list[dict[str, str]]) -> str | None:
    """The reply to one of the past-paper prompts, or ``None`` if this is some other prompt."""
    system = messages[0]["content"] if messages else ""
    user = next((m["content"] for m in messages if m["role"] == "user"), "")
    if system.startswith("You extract exam questions"):
        return _extract(user)
    if system.startswith("You classify university exam questions"):
        return _classify(user)
    if system.startswith("You write one exam question"):
        return _mock(user, len(messages))
    return None


def _extract(user: str) -> str:
    match = _PAPER.search(user)
    questions = []
    for line in (match.group(1) if match else "").splitlines():
        for sentence in split_sentences(line.strip("-# ").strip()):
            if _ASKS.match(sentence):
                questions.append({"number": str(len(questions) + 1), "sub": "", "part": "",
                                  "marks": None, "text": sentence})
    return json.dumps({"questions": questions})


def _classify(user: str) -> str:
    items = []
    for number, text in _CLASSIFY_LINE.findall(user):
        question_type, _confident = classify_type(text)
        level, _source = classify_bloom(text, question_type)
        items.append({"id": int(number), "question_type": question_type, "bloom_level": level})
    return json.dumps({"items": items})


def _mock(user: str, turn: int) -> str:
    notes = _NOTES.search(user)
    if not notes:
        return "{}"
    section = notes.group("section")
    kind = notes.group("type")
    marks = float(notes.group("marks"))
    sentences = [
        (sid, text) for sid, text, _page in parse_numbered_notes(notes.group("body"))
        if len(text.split()) >= 6 and "$" not in text and not text.startswith(("|", "-"))
    ]
    if not sentences:
        return "{}"
    avoid = user.split("Do not repeat these questions", 1)[-1] if "Do not repeat" in user else ""
    digest = int(hashlib.md5(f"{section}{kind}{avoid}{turn}".encode("utf-8")).hexdigest()[:8], 16)
    sid, sentence = sentences[digest % len(sentences)]
    claim = sentence.rstrip(".")
    a, b = 2 + digest % 7, 3 + digest % 5
    base = {"question_type": kind, "bloom_level": _BLOOM_BY_TYPE[kind], "answer_expression": ""}
    cite = {"text": f"The notes state: {sentence}", "evidence_ids": [sid]}
    if kind == "numerical":
        body = {
            "question": (f"The notes on {section} state that {claim}. In an example two measured "
                         f"values are {a} and {b}. Compute their product."),
            "steps": [cite, {"text": f"Multiply the two values: ${a} \\times {b} = {a * b}$.",
                             "evidence_ids": []}],
            "final_answer": f"The product is {a * b}.",
            "answer_expression": f"{a} * {b}",
        }
    elif kind == "algorithm":
        body = {
            "question": (f"Apply the method from {section} to a small example of your own and "
                         f"trace it step by step, given that {claim}."),
            "steps": [cite, {"text": "Apply this to the first input and record the state.", "evidence_ids": []},
                      {"text": "Repeat for the next input and compare the states.", "evidence_ids": []}],
            "final_answer": "The trace ends once every input has been processed.",
        }
    elif kind == "derivation":
        body = {
            "question": f"Starting from the statement that {claim}, derive the result discussed in {section}.",
            "steps": [cite, {"text": "Write the statement as an equation: $y = f(x)$.", "evidence_ids": []},
                      {"text": "Rearrange it to reach the result: $x = f^{-1}(y)$.", "evidence_ids": []}],
            "final_answer": f"This gives the result stated in {section}.",
        }
    elif kind == "diagram":
        body = {
            "question": f"Draw a labelled diagram for {section} and explain it, given that {claim}.",
            "steps": [cite, {"text": "Label each part named in that statement.", "evidence_ids": []}],
            "final_answer": "The diagram labels every part and shows how they connect.",
        }
    elif kind == "case_based":
        body = {
            "question": (f"A small company is building a product and its engineers read that {claim}. "
                         f"They must decide whether the ideas in {section} apply to their data. "
                         "Advise them and justify your recommendation."),
            "steps": [cite, {"text": "Compare that statement with the company's situation.", "evidence_ids": []}],
            "final_answer": f"The ideas in {section} apply when that statement holds for the data.",
        }
    elif kind == "explain_compare":
        body = {
            "question": f"Explain the key idea of {section}, including why {claim}.",
            "steps": [cite, {"text": "Give the reason in your own words.", "evidence_ids": []}],
            "final_answer": sentence,
        }
    else:
        body = {
            "question": f"State the main point of {section} about the claim that {claim}.",
            "steps": [cite],
            "final_answer": sentence,
        }
    half = marks / 2
    body["marking"] = [{"point": "Correct method", "marks": half}, {"point": "Correct result", "marks": half}]
    return json.dumps({**base, **body})


__all__ = ["offline_paper_reply"]
