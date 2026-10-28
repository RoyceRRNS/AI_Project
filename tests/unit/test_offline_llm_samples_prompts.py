import json

import pymupdf

from course_notes_rag.generation import prompts
from course_notes_rag.generation.citations import verify_citations
from course_notes_rag.generation.offline_llm import ExtractiveLLM
from course_notes_rag.generation.quiz_engine import QuizQuestionDraft, check_draft, draft_schema
from course_notes_rag.models import BBox, Block, RetrievedChunk
from course_notes_rag.samples import SAMPLE_FILES

BLOCKS = [
    Block("heading", "Gradient Clipping", 3, BBox(3, 10, 10, 100, 20), 2),
    Block("text", "Exploding gradients are handled with gradient clipping before the update. "
                  "Clipping rescales the gradient when its norm exceeds a threshold value.", 3,
          BBox(3, 10, 30, 400, 80)),
    Block("text", "Clipping does not help with vanishing gradients in recurrent networks at all.", 4,
          BBox(4, 10, 30, 400, 80)),
]
CHUNK = RetrievedChunk("p", "d", "c", "Lecture_07_RNNs.pdf", "Lecture 7", "Clipping", BLOCKS, 3, 4)


def test_format_context_has_page_markers_and_attributes():
    ctx = prompts.format_context([CHUNK])
    assert ctx.startswith('<source id="1" pdf="Lecture_07_RNNs.pdf" pages="3-4"')
    assert "[p. 3]" in ctx and "[p. 4]" in ctx and ctx.endswith("</source>")
    assert "## Gradient Clipping" in ctx


def test_chat_messages_structure():
    msgs = prompts.chat_messages("CS229", "What is clipping?", [CHUNK], [{"role": "user", "content": "hi"}])
    assert [m["role"] for m in msgs] == ["system", "user", "user"]
    assert "CS229" in msgs[0]["content"]


def test_quiz_messages_include_level_guide_and_avoid_list():
    msgs = prompts.quiz_messages("Application", "clipping", "a.pdf", "Clipping", BLOCKS, ["Old question?"])
    assert "Application/calculation" in msgs[0]["content"]
    assert "sign_inversion" in msgs[0]["content"] and "evidence_ids" in msgs[0]["content"]
    assert "- Old question?" in msgs[1]["content"] and '<notes pdf="a.pdf"' in msgs[1]["content"]
    assert "[S1] Exploding gradients" in msgs[1]["content"] and "[p. 4]" in msgs[1]["content"]


def test_offline_llm_answers_with_valid_citations():
    llm = ExtractiveLLM()
    msgs = prompts.chat_messages("CS229", "How does gradient clipping handle exploding gradients?", [CHUNK], [])
    answer = "".join(llm.stream_chat(msgs))
    report = verify_citations(answer, [CHUNK])
    assert report.citations and report.invalid_count == 0
    assert "[Source: Lecture_07_RNNs.pdf, p. 3]" in answer


def test_offline_llm_not_found():
    msgs = prompts.chat_messages("CS229", "photosynthesis chloroplast", [CHUNK], [])
    assert llm_answer(msgs) == prompts.NOT_FOUND_MESSAGE


def llm_answer(msgs):
    return "".join(ExtractiveLLM().stream_chat(msgs))


def test_offline_llm_quiz_json_is_valid_and_grounded():
    for level in ("Recall", "Conceptual", "Application"):
        msgs = prompts.quiz_messages(level, "clipping", "a.pdf", "Clipping", BLOCKS, [])
        raw = ExtractiveLLM().chat(msgs, json_schema=draft_schema(level))
        draft = QuizQuestionDraft.model_validate_json(raw)
        problems, evidence = check_draft(draft, level, BLOCKS)
        assert problems == [], problems
        assert evidence.page in (3, 4)


def test_offline_llm_helpers():
    llm = ExtractiveLLM()
    condensed = llm.chat(prompts.condense_messages("and LSTMs?", [{"role": "user", "content": "rnn?"}]))
    assert condensed == "and LSTMs?"
    assert llm.chat(prompts.multi_query_messages("gradient clipping norm", 2)).count("\n") == 1
    assert llm.chat(prompts.hyde_messages("q")) == "q"
    assert ExtractiveLLM(healthy=False).health()["ok"] is False
    assert json.loads(llm.chat([{"role": "user", "content": "no notes"}], json_schema=draft_schema("Recall"))) == {}


def test_sample_pdfs_are_built(sample_pdfs, invalid_pdfs):
    assert set(sample_pdfs) == set(SAMPLE_FILES)
    slides = pymupdf.open(sample_pdfs["Lecture_02_Probability_Slides.pdf"])
    assert slides[0].rect.width > slides[0].rect.height
    scan = pymupdf.open(sample_pdfs["Handwritten_Gradient_Descent.pdf"])
    assert scan[0].get_text().strip() == "" and scan[0].get_images()
    assert invalid_pdfs["empty.pdf"].stat().st_size == 0
    assert pymupdf.open(invalid_pdfs["encrypted.pdf"]).needs_pass
