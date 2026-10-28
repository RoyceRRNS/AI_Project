"""The full production stack: Docling, BGE-M3, bge-reranker-v2-m3, Qdrant, BM25 and Qwen via Ollama.

Run with:  pytest -m live
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from course_notes_rag.benchmark import format_report, run_benchmark, write_report
from course_notes_rag.config.settings import Settings
from course_notes_rag.generation.quiz_engine import QuizQuestion, QuizRequest
from course_notes_rag.samples import SAMPLE_COURSE
from course_notes_rag.services import build_services
from tests.conftest import RNN, SCAN, SLIDES, SVM

pytestmark = pytest.mark.live
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def live(tmp_path_factory, sample_pdfs):
    services = build_services(Settings(profile="production", storage_dir=tmp_path_factory.mktemp("live")))
    health = services.llm.health()
    if not health["ok"]:
        services.close()
        pytest.fail(f"Ollama/Qwen is not ready: {health['detail']}")
    course = services.db.create_course(SAMPLE_COURSE["name"], SAMPLE_COURSE["code"])
    docs = {}
    for name in (SVM, RNN, SLIDES, SCAN):
        doc = services.ingestion.submit_upload(course["id"], name, sample_pdfs[name].read_bytes())
        services.ingestion.wait(doc["id"], timeout=1800)
        docs[name] = services.db.get_document(doc["id"], with_markdown=True)
    yield {"services": services, "course_id": course["id"], "docs": docs}
    services.close()


def test_docling_ingestion_preserves_math_tables_and_ocr(live):
    docs = live["docs"]
    for name, doc in docs.items():
        assert doc["status"] == "ready", f"{name}: {doc['error']}"
        assert doc["engine"] == "docling", doc["engine"]
    svm_md = docs[SVM]["markdown"]
    assert "$$" in svm_md, "equations should be emitted as LaTeX blocks"
    assert "\\frac" in svm_md or "\\min" in svm_md, "the rendered equation image should be converted to LaTeX"
    assert "Gaussian (RBF)" in svm_md and "|" in svm_md
    assert docs[SLIDES]["is_slides"] is True
    assert "learning rate" in docs[SCAN]["markdown"].lower(), "handwritten scan should be OCRed"


def test_retrieval_benchmark(live):
    report = run_benchmark(live["services"].retriever, live["course_id"])
    path = write_report(report, ROOT / "benchmarks" / "results", "live")
    print("\n" + format_report(report) + f"\nreport: {path}")
    assert report["summary"]["hit_at_5"] >= 0.8, json.dumps(report["summary"])
    assert report["summary"]["mrr"] >= 0.7, json.dumps(report["summary"])


def test_grounded_chat_with_qwen(live):
    s = live["services"]
    events = list(s.chat.stream_answer(
        live["course_id"], "CS229-lite",
        "Why does the vanishing gradient problem happen in RNNs and how do LSTMs fix it?",
    ))
    by = {e.type: e.data for e in events}
    assert "error" not in by, by.get("error")
    answer = by["done"]["answer"]
    print("\n" + answer)
    report = by["citations"]
    assert report["valid_count"] >= 1, report
    assert report["invalid_count"] <= max(1, report["valid_count"] // 4), report
    assert "Lecture_07_RNNs.pdf" in answer


def test_not_found_fallback_with_real_reranker(live):
    events = list(live["services"].chat.stream_answer(
        live["course_id"], "CS229-lite", "What is the capital of Australia and when was it founded?"
    ))
    assert any(e.type == "not_found" for e in events)


def test_quiz_generation_with_qwen(live):
    s = live["services"]
    events = list(s.quiz.generate(QuizRequest(
        course_id=live["course_id"], topic="LSTM forget gate and vanishing gradients", num_questions=3, seed=3,
    )))
    errors = [e.data for e in events if e.type == "error"]
    assert not errors, errors
    quiz_id = events[0].data["quiz_id"]
    rows = s.db.get_quiz_questions(quiz_id)
    assert len(rows) >= 2, [e.data for e in events if e.type == "warning"]
    for row in rows:
        q = QuizQuestion.model_validate(row["payload"])
        print(f"\n[{q.bloom_level}] {q.question_stem}\n  quote (p.{q.source_page}): {q.source_quote}")
        assert sum(o.is_correct for o in q.options) == 1
        parent = s.db.get_parents([q.parent_id])[q.parent_id]
        def flat(s: str) -> str:  # formulas are quoted inline ($...$) but stored as $$...$$
            return " ".join(s.replace("$", " ").split()).lower()

        assert flat(q.source_quote) in flat(parent.text)
        graded = s.quiz.answer(quiz_id, q.question_id, q.correct_label)
        assert graded["is_correct"] and graded["mastery_updated"]
