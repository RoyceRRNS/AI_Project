"""Prompts for reading question papers and writing exam-style questions with worked solutions."""

from __future__ import annotations

from course_notes_rag.generation.evidence import format_evidence
from course_notes_rag.models import Block
from course_notes_rag.papers.taxonomy import BLOOM_LEVELS, QUESTION_TYPES

EXTRACT_SYSTEM = """You extract exam questions from the text of a university question paper. \
Return one entry per question or sub-question that a student must answer, as JSON matching the \
schema.

Rules:
- Copy each question's text exactly as printed. Never rephrase, summarise or add words.
- number is the printed question number. sub is the printed sub-part such as "a" or "b", or empty.
- part is the printed part or section letter such as "A", or empty.
- marks is the number of marks printed for that question, or null if none is printed.
- Skip the header, instructions and anything that is not a question."""

CLASSIFY_SYSTEM = """You classify university exam questions. For each numbered question choose \
exactly one question_type and one bloom_level, and return JSON matching the schema.

question_type:
- definition: define, state or list something; short recall of theory.
- explain_compare: explain how or why, describe, discuss, compare or differentiate.
- derivation: derive a result or prove a statement.
- numerical: compute a value from the numbers given in the question.
- algorithm: apply or trace a named algorithm or procedure step by step on an input.
- diagram: the answer is mainly a drawn, labelled diagram.
- case_based: a realistic scenario the student must analyse.

bloom_level: Remember, Understand, Apply, Analyze, Evaluate or Create.

Return one item for every question id, using the ids as given."""

TYPE_GUIDE: dict[str, str] = {
    "definition": (
        "Short theory: ask for a definition, a statement or a list that the notes give directly."
    ),
    "explain_compare": (
        "Ask the student to explain how or why something works, or to compare two ideas from the "
        "notes. The solution makes the key points in order."
    ),
    "derivation": (
        "Ask for a derivation or proof that the notes support. Start from what is given and reach "
        "the result, with one piece of algebra or reasoning per solution step."
    ),
    "numerical": (
        "Choose small concrete numbers and ask for a computed value using a formula or method from "
        "the notes. State every number in the question. Work the arithmetic carefully, one "
        "calculation per step, and make sure the final value is right."
    ),
    "algorithm": (
        "Give a small concrete input and ask the student to apply or trace a procedure from the "
        "notes, showing the state after each step of the procedure."
    ),
    "diagram": (
        "Ask the student to draw a labelled diagram of something the notes describe and to explain "
        "it. The solution lists what the diagram must show and how the parts connect."
    ),
    "case_based": (
        "Describe a short realistic scenario in at least two sentences, then ask which idea from "
        "the notes applies and why, or how to solve the problem it raises."
    ),
}

MOCK_SYSTEM = """You write one exam question with a fully worked model solution for the course \
"{course}", grounded strictly in the lecture notes provided. Produce ONE JSON object that matches \
the schema.

Requirements:
- question_type must be "{question_type}". {guide}
- The question is worth {marks} marks: {depth}
- bloom_level must be one of {levels}, whichever fits the question you wrote.
- question: new and self-contained, in the style of the past questions shown, never a copy of \
them. It must be answerable from the notes alone.
- steps: the worked solution a student could write in the exam, in order. When a step uses a fact, \
definition or formula from the notes, put the numbers of those [S#] sentences in its evidence_ids. \
A purely arithmetic step may have an empty list, but at least one step must cite the notes.
- Do not write citations such as [Source: ...] in the text; they are added from evidence_ids.
- final_answer: the result in one or two sentences.
- answer_expression: for a numerical question, the calculation behind the final value as a plain \
arithmetic expression in Python syntax, such as (3 + 4) * 0.5 or sqrt(2) / 4. Otherwise "".
- marking: how the marks are awarded, as short points whose marks add up to exactly {marks}.
- Use LaTeX with $...$ for every mathematical expression."""


def _depth(marks: float) -> str:
    if marks <= 3:
        return "keep it short, with 1 to 2 solution steps."
    if marks <= 8:
        return "expect a medium answer, with 3 to 4 solution steps."
    return "expect a long answer, with 5 to 7 solution steps."


def extract_messages(paper_text: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": EXTRACT_SYSTEM},
        {"role": "user", "content": f"<paper>\n{paper_text}\n</paper>"},
    ]


def classify_messages(items: list[tuple[int, str]]) -> list[dict[str, str]]:
    body = "\n".join(f"Q{i}: {text}" for i, text in items)
    return [
        {"role": "system", "content": CLASSIFY_SYSTEM},
        {"role": "user", "content": f"<questions>\n{body}\n</questions>"},
    ]


def classify_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "question_type": {"type": "string", "enum": list(QUESTION_TYPES)},
                        "bloom_level": {"type": "string", "enum": list(BLOOM_LEVELS)},
                    },
                    "required": ["id", "question_type", "bloom_level"],
                },
            }
        },
        "required": ["items"],
    }


def mock_messages(
    course: str, question_type: str, marks: float, pdf_name: str, section: str,
    blocks: list[Block], examples: list[str], avoid: list[str],
) -> list[dict[str, str]]:
    system = MOCK_SYSTEM.format(
        course=course, question_type=question_type, guide=TYPE_GUIDE[question_type],
        marks=f"{marks:g}", depth=_depth(marks), levels=", ".join(BLOOM_LEVELS),
    )
    style = (
        "Past questions from this exam, showing how questions are phrased:\n"
        + "\n".join(f"- {q}" for q in examples) + "\n\n"
        if examples else ""
    )
    repeat = (
        "\n\nDo not repeat these questions:\n" + "\n".join(f"- {q}" for q in avoid) if avoid else ""
    )
    notes = format_evidence(blocks)
    user = (
        f"{style}<notes pdf=\"{pdf_name}\" section=\"{section}\" type=\"{question_type}\" "
        f"marks=\"{marks:g}\">\n{notes}\n</notes>{repeat}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
