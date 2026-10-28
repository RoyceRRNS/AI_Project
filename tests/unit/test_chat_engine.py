import pytest

from course_notes_rag.generation import prompts
from course_notes_rag.generation.chat_engine import ChatRequestError, GroundedChatEngine
from course_notes_rag.generation.llm import FakeLLM, LLMError, LLMUnavailableError
from course_notes_rag.models import BBox, Block, RetrievedChunk
from course_notes_rag.retrieval.search import RetrievalResult


def chunk():
    blocks = [
        Block("heading", "3.2 KKT Conditions", 2, BBox(2, 50, 50, 300, 70), 3),
        Block("text", "Complementary slackness requires alpha_i g_i(w) = 0.", 2, BBox(2, 50, 80, 500, 120)),
        Block("formula", r"\alpha_i^* g_i(w^*) = 0", 3, BBox(3, 100, 40, 400, 60)),
    ]
    return RetrievedChunk("p1", "d1", "c1", "Lecture_04_SVMs.pdf", "Lecture 4: SVMs", "KKT Conditions",
                          blocks, 2, 3, rerank_score=0.9)


class FakeRetriever:
    def __init__(self, chunks):
        self.chunks = chunks
        self.queries = []

    def retrieve(self, query, course_id, **kw):
        self.queries.append(query)
        return RetrievalResult(query, [query], self.chunks, not self.chunks, list(self.chunks),
                               {"total_ms": 1.0})


ANSWER = ("Complementary slackness requires each multiplier times its constraint to vanish "
          "[Source: Lecture_04_SVMs.pdf, p. 2].")


def run(engine, question="What is complementary slackness?", **kw):
    return list(engine.stream_answer("c1", "CS229", question, **kw))


def test_event_sequence_and_grounded_answer():
    llm = FakeLLM([ANSWER])
    events = run(GroundedChatEngine(FakeRetriever([chunk()]), llm))
    types = [e.type for e in events]
    assert types[0] == "status" and types[1] == "sources" and types[2] == "status"
    assert types[-2:] == ["citations", "done"]
    assert "".join(e.data["text"] for e in events if e.type == "token") == ANSWER
    sources = events[1].data["sources"]
    assert sources[0]["pdf_name"] == "Lecture_04_SVMs.pdf" and sources[0]["page_start"] == 2
    citations = events[-2].data
    assert citations["valid_count"] == 1 and citations["grounded"] is True
    assert events[-1].data == {"answer": ANSWER, "not_found": False}


def test_prompt_contains_rules_context_and_page_markers():
    llm = FakeLLM([ANSWER])
    run(GroundedChatEngine(FakeRetriever([chunk()]), llm))
    system, user = llm.calls[0]["messages"][0]["content"], llm.calls[0]["messages"][-1]["content"]
    assert "ONLY" in system and "[Source: <pdf file name>, p. <page>]" in system
    assert prompts.NOT_FOUND_MESSAGE in system and "$$...$$" in system
    assert '<source id="1" pdf="Lecture_04_SVMs.pdf" pages="2-3"' in user
    assert "[p. 2]" in user and "[p. 3]" in user and "$$\n\\alpha_i^*" in user
    assert "Question: What is complementary slackness?\n\nReminder:" in user
    assert user.endswith(prompts.CHAT_REMINDER)


def test_not_found_short_circuits_without_calling_the_llm():
    llm = FakeLLM([])
    events = run(GroundedChatEngine(FakeRetriever([]), llm))
    assert [e.type for e in events] == ["status", "sources", "not_found", "done"]
    assert events[-1].data["not_found"] is True
    assert llm.calls == []


def test_llm_saying_not_found_is_flagged():
    events = run(GroundedChatEngine(FakeRetriever([chunk()]), FakeLLM([prompts.NOT_FOUND_MESSAGE])))
    assert events[-1].data["not_found"] is True


@pytest.mark.parametrize("question", ["", "   ", "x" * 2001])
def test_invalid_questions(question):
    engine = GroundedChatEngine(FakeRetriever([chunk()]), FakeLLM([]))
    with pytest.raises(ChatRequestError):
        run(engine, question)


def test_llm_failure_mid_stream_becomes_error_event():
    class Breaking(FakeLLM):
        def stream_chat(self, messages, *, temperature=None):
            yield "partial"
            raise LLMUnavailableError("Ollama died")

    events = run(GroundedChatEngine(FakeRetriever([chunk()]), Breaking()))
    assert events[-1].type == "error" and "Ollama died" in events[-1].data["message"]
    assert any(e.type == "token" for e in events)


def test_history_is_condensed_and_trimmed():
    llm = FakeLLM(["What does complementary slackness imply for SVMs?", ANSWER])
    retriever = FakeRetriever([chunk()])
    history = [{"role": "user", "content": f"q{i}"} if i % 2 == 0 else
               {"role": "assistant", "content": f"a{i}"} for i in range(10)]
    history.append({"role": "system", "content": "ignored"})
    run(GroundedChatEngine(retriever, llm, history_turns=2), "and for SVMs?", history=history)
    assert retriever.queries == ["What does complementary slackness imply for SVMs?"]
    answer_messages = llm.calls[1]["messages"]
    assert [m["content"] for m in answer_messages[1:-1]] == ["q6", "a7", "q8", "a9"]


def test_condense_failure_falls_back_to_raw_question():
    llm = FakeLLM([LLMError("down"), ANSWER])
    retriever = FakeRetriever([chunk()])
    run(GroundedChatEngine(retriever, llm), "and then?", history=[{"role": "user", "content": "kkt?"}])
    assert retriever.queries == ["and then?"]


def test_retrieval_llm_error_becomes_error_event():
    class Exploding(FakeRetriever):
        def retrieve(self, *a, **k):
            raise LLMUnavailableError("expansion model offline")

    events = run(GroundedChatEngine(Exploding([]), FakeLLM([])))
    assert [e.type for e in events] == ["status", "error"]
