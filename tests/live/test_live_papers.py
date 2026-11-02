"""Past papers on the production stack: Docling, BGE-M3, bge-reranker-v2-m3 and Qwen via Ollama.

Run with:  pytest -m live tests/live/test_live_papers.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from course_notes_rag.config.settings import Settings
from course_notes_rag.papers.mock import MockRequest
from course_notes_rag.papers.samples import build_sample_papers
from course_notes_rag.samples import SAMPLE_COURSE
from course_notes_rag.services import build_services
from tests.conftest import RNN, SCAN, SLIDES, SVM

pytestmark = pytest.mark.live
REPORT = Path(__file__).resolve().parents[2] / "benchmarks" / "results"


@pytest.fixture(scope="module")
def live(tmp_path_factory, sample_pdfs):
    settings = Settings(profile="production", storage_dir=tmp_path_factory.mktemp("live-papers"))
    services = build_services(settings)
    health = services.llm.health()
    if not health["ok"]:
        services.close()
        pytest.fail(f"Ollama/Qwen is not ready: {health['detail']}")
    course = services.db.create_course(SAMPLE_COURSE["name"], SAMPLE_COURSE["code"])
    for name in (SVM, RNN, SLIDES, SCAN):
        doc = services.ingestion.submit_upload(course["id"], name, sample_pdfs[name].read_bytes())
        services.ingestion.wait(doc["id"], timeout=1800)
        assert services.db.get_document(doc["id"])["status"] == "ready", name
    papers = {}
    for name, path in build_sample_papers(tmp_path_factory.mktemp("paper-pdfs")).items():
        paper = services.papers.submit_upload(course["id"], name, path.read_bytes())
        services.papers.wait(paper["id"], timeout=1800)
        papers[name] = services.papers.get_paper(paper["id"])
    yield {"services": services, "course_id": course["id"], "papers": papers}
    services.close()


def by_label(paper):
    return {q["label"]: q for q in paper["questions"]}


def test_docling_reads_every_layout(live):
    papers = live["papers"]
    for name, paper in papers.items():
        assert paper["status"] == "ready", f"{name}: {paper['error']}"
        assert paper["engine"].startswith("docling"), (name, paper["engine"])
    print(json.dumps({n: [q["label"] for q in p["questions"]] for n, p in papers.items()}, indent=1))
    text = papers["End_Semester_2024.pdf"]
    assert len(text["questions"]) == 11
    assert by_label(text)["7(a)"]["marks"] == 10 and by_label(text)["7(a)"]["choice_group"] == "7"
    assert {q["part"] for q in text["questions"]} == {"A", "B"}
    table = papers["CAT1_Fall_2025.pdf"]
    assert [q["marks"] for q in table["questions"]] == [4, 6, 10, 10]
    assert table["questions"][0]["bloom_source"] == "paper"
    scan = papers["End_Semester_May_2022_Scan.pdf"]
    assert [q["marks"] for q in scan["questions"]] == [5, 5, 10], "a scanned paper must be OCRed"


def test_questions_map_to_the_right_notes(live):
    questions = by_label(live["papers"]["End_Semester_2024.pdf"])
    assert "Lecture_02" in (questions["7(a)"]["notes_pdf"] or ""), "Bayes belongs to the probability slides"
    assert "Lecture_04" in (questions["6(a)"]["notes_pdf"] or ""), "the SVM dual belongs to the SVM lecture"
    assert "Lecture_07" in (questions["7(b)"]["notes_pdf"] or ""), "the LSTM belongs to the RNN lecture"
    scan = by_label(live["papers"]["End_Semester_May_2022_Scan.pdf"])
    assert "Lecture_02" in (scan["3"]["notes_pdf"] or ""), "numbers must not hide a Bernoulli question"


def test_a_question_outside_the_notes_is_a_gap_not_a_guess(live):
    classifier = live["services"].papers.classifier
    question = {"id": "x", "text": "Explain how chloroplasts carry out photosynthesis."}
    assert classifier.map_topics([question], live["course_id"]) == {"x": None}


def test_blueprint_finds_the_exam_pattern(live):
    blueprint = live["services"].papers.blueprint(live["course_id"])
    print(json.dumps(
        {"summary": blueprint["summary"], "typical": blueprint["typical"]["signature"],
         "recurring": [(r["text"], r["papers"], [i["text"] for i in r["instances"]]) for r in blueprint["recurring"]],
         "gaps": [g["text"] for g in blueprint["gaps"]]},
        indent=1, ensure_ascii=False,
    ))
    assert blueprint["summary"]["coverage"] >= 0.7
    assert blueprint["typical"]["paper_count"] == 2 and blueprint["typical"]["total_marks"] == 40
    assert blueprint["gaps"] == []
    clusters = [[i["text"] for i in r["instances"]] for r in blueprint["recurring"]]
    def together(*words):
        return any(all(any(w in text for text in c) for w in words) for c in clusters)

    assert together("A disease affects", "Given P(D)"), "two wordings of one Bayes problem"
    assert together("structure of an LSTM cell", "block diagram of an LSTM cell")
    assert together("geometric margin of a training example", "geometric margin of a training set")
    assert not together("functional margin", "geometric margin"), "different terms, different questions"
    assert not together("stochastic gradient descent", "learning rate")


def test_plan_is_built_from_the_papers(live):
    plan = live["services"].papers.plan(live["course_id"], days=5)
    assert plan["items"] and plan["items"][0]["tier"] == "must"
    assert plan["schedule"][-1]["title"] == "Timed mock paper"


def test_qwen_writes_a_grounded_practice_set(live):
    services = live["services"]
    req = MockRequest(course_id=live["course_id"], kind="practice", num_questions=3,
                      question_types=["numerical", "algorithm", "explain_compare"], seed=7)
    events = list(services.papers.generate_mock(req))
    questions = [e.data["question"] for e in events if e.type == "question"]
    warnings = [e.data["message"] for e in events if e.type in ("warning", "error")]
    fields = ("question_type", "marks", "section_title", "question", "solution", "final_answer",
              "answer_check", "marking", "repairs")
    summary = {"model": services.llm.model, "warnings": warnings,
               "questions": [{k: q[k] for k in fields} for q in questions]}
    REPORT.mkdir(parents=True, exist_ok=True)
    (REPORT / "papers_live_practice.json").write_text(
        json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8",
    )
    print(json.dumps(summary, indent=1))  # ASCII: a Windows console cannot print every symbol
    assert len(questions) >= 2, warnings
    for q in questions:
        cites = q["report"]["citations"]
        assert cites and all(c["valid"] for c in cites), q
        assert q["evidence"] and q["evidence"]["quote"]
    numerical = [q for q in questions if q["question_type"] == "numerical"]
    for q in numerical:
        assert q["answer_check"]["value"] is not None
