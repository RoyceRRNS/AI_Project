"""Prompts for grounded QA, query expansion and Bloom's-taxonomy quiz generation."""

from __future__ import annotations

from course_notes_rag.generation.evidence import format_evidence
from course_notes_rag.models import Block, RetrievedChunk

NOT_FOUND_MESSAGE = (
    "Not Found in Course Notes: I couldn't find anything in your uploaded notes that answers "
    "this question."
)

CHAT_SYSTEM = """You are a meticulous teaching assistant for the course "{course}". Answer the \
student's question using ONLY the course-note excerpts inside <context>.

Rules:
1. Use only facts, definitions, formulas and derivations that appear in the context. Never add \
outside knowledge.
2. Cite EVERY sentence that states a fact or a formula, at the end of that sentence, in exactly \
this form: [Source: <pdf file name>, p. <page>]. Take the file name from the pdf attribute of the \
<source> and the page from the nearest [p. N] marker above the information you used. One \
citation per paragraph is not enough.
3. Write all math in LaTeX with dollar signs: $...$ inline, and $$...$$ on its own lines for \
display equations (never \\( \\) or \\[ \\]). Put the citation on the line right after a display \
equation.
4. If the context does not contain the answer, reply with exactly: "{not_found}"
5. Be clear and well structured: short paragraphs or bullet lists, define symbols, and walk \
through derivations step by step.

Example of the citation style:
The margin is the distance from a point to the decision boundary [Source: Lecture_04.pdf, p. 3]. \
Maximizing it gives the optimal margin classifier [Source: Lecture_04.pdf, p. 4]."""

CONDENSE_SYSTEM = """Rewrite the student's latest message into a single standalone question \
that can be understood without the conversation. Keep all technical terms and symbols. Output \
only the rewritten question."""

MULTI_QUERY_SYSTEM = """You help search a student's lecture notes. Rewrite the question into \
{n} different search queries that use alternative wording, synonyms and the technical terms a \
lecturer would use. Output one query per line with no numbering or extra text."""

HYDE_SYSTEM = """Write a short passage (3-5 sentences) as it would appear in university lecture \
notes that answers the question. Use precise technical terminology and LaTeX for math. Output \
only the passage."""

BLOOM_GUIDE = {
    "Recall": (
        "Recall: test whether the student remembers a specific definition, fact, formula or "
        "statement as written in the notes."
    ),
    "Conceptual": (
        "Conceptual understanding: test whether the student understands why or how something "
        "works - relationships, conditions, implications or comparisons. The answer must follow "
        "from the notes but must not be a verbatim lookup."
    ),
    "Application": (
        "Application/calculation: give a small concrete scenario or numbers and ask the student "
        "to apply a formula, algorithm or theorem from the notes. Work out the correct answer "
        "carefully so that exactly one option is correct."
    ),
}

QUIZ_SYSTEM = """You write high-quality multiple-choice questions for university students, \
grounded strictly in the provided lecture notes. Produce ONE question as a JSON object that \
matches the schema.

Requirements:
- bloom_level must be "{level}". {guide}
- question_stem: clear, self-contained and answerable only from the notes.
- Exactly 4 options. Exactly ONE option has is_correct=true and trap_type="correct".
- The 3 incorrect options are plausible distractors. Build each with one of these traps, using \
each trap at most once where the content allows:
  * sign_inversion: flip a sign, exponent or inequality, or swap terms in a formula from the notes.
  * boundary_violation: apply a theorem or method where its prerequisites (e.g. convexity, \
independence, differentiability) do not hold.
  * terminology_swap: confuse closely related concepts from the same lecture.
- misconception_explanation: for the correct option explain why it is right; for each \
distractor name the specific misconception it targets.
- hint: a nudge toward the right idea that does not reveal the answer.
- evidence_ids: the numbers of the 1 to 3 note sentences, shown as [S#], that prove the \
correct answer. Choose sentences that state the fact directly.
- Use LaTeX with $...$ for every mathematical expression in the stem and options."""


def _block_lines(blocks: list[Block]) -> list[str]:
    lines: list[str] = []
    page: int | None = None
    for block in blocks:
        if block.page != page:
            lines.append(f"[p. {block.page}]")
            page = block.page
        lines.append(block.to_markdown())
    return lines


def format_context(chunks: list[RetrievedChunk]) -> str:
    parts = []
    for i, chunk in enumerate(chunks, start=1):
        header = (
            f'<source id="{i}" pdf="{chunk.pdf_name}" pages="{chunk.page_start}-{chunk.page_end}" '
            f'lecture="{chunk.lecture_title}" section="{chunk.section_title}">'
        )
        parts.append("\n".join([header, *_block_lines(chunk.blocks), "</source>"]))
    return "\n\n".join(parts)


def chat_messages(
    course: str, question: str, chunks: list[RetrievedChunk], history: list[dict[str, str]]
) -> list[dict[str, str]]:
    messages = [
        {"role": "system", "content": CHAT_SYSTEM.format(course=course, not_found=NOT_FOUND_MESSAGE)}
    ]
    for turn in history:
        messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append(
        {
            "role": "user",
            "content": (
                f"<context>\n{format_context(chunks)}\n</context>\n\nQuestion: {question}\n\n"
                f"{CHAT_REMINDER}"
            ),
        }
    )
    return messages


# Small models follow formatting rules far more reliably when repeated in the user turn.
CHAT_REMINDER = (
    "Reminder: answer only from the context. End EVERY sentence with its own citation like "
    "[Source: file.pdf, p. 3], and write math as $...$ or $$...$$."
)


def condense_messages(question: str, history: list[dict[str, str]]) -> list[dict[str, str]]:
    convo = "\n".join(f"{t['role']}: {t['content'][:600]}" for t in history)
    return [
        {"role": "system", "content": CONDENSE_SYSTEM},
        {"role": "user", "content": f"Conversation:\n{convo}\n\nLatest message: {question}"},
    ]


def multi_query_messages(question: str, n: int) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": MULTI_QUERY_SYSTEM.format(n=n)},
        {"role": "user", "content": question},
    ]


def hyde_messages(question: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": HYDE_SYSTEM},
        {"role": "user", "content": question},
    ]


def quiz_messages(
    level: str, topic: str, pdf_name: str, section: str, blocks: list[Block],
    avoid: list[str],
) -> list[dict[str, str]]:
    notes = format_evidence(blocks)
    avoid_text = (
        "\n\nDo not repeat these questions already in the quiz:\n" + "\n".join(f"- {q}" for q in avoid)
        if avoid
        else ""
    )
    return [
        {"role": "system", "content": QUIZ_SYSTEM.format(level=level, guide=BLOOM_GUIDE[level])},
        {
            "role": "user",
            "content": (
                f"Topic focus: {topic}\n\n<notes pdf=\"{pdf_name}\" section=\"{section}\">\n{notes}\n"
                f"</notes>{avoid_text}"
            ),
        },
    ]


def quiz_feedback_message(problems: list[str]) -> dict[str, str]:
    bullet = "\n".join(f"- {p}" for p in problems)
    return {
        "role": "user",
        "content": f"Your previous JSON had these problems:\n{bullet}\nReturn a corrected JSON object.",
    }
