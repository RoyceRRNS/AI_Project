"""End-to-end user journeys over HTTP, through every layer of the real stack.

Runs with the ``test`` profile: real PDF parsing (PyMuPDF + RapidOCR), real chunking,
Qdrant, BM25, RRF and SQLite, with offline stand-ins for the neural models and the LLM.
The same journeys run against the real models in ``tests/live``.
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from course_notes_rag.api.server import create_app
from course_notes_rag.generation.quiz_engine import QuizQuestion
from tests.conftest import RNN, SCAN, SLIDES, SVM
from tests.unit.test_api import parse_sse


@pytest.fixture
def client(services):
    with TestClient(create_app(services)) as c:
        yield c


def poll_until_ready(client, course_id, expected, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        docs = client.get(f"/api/courses/{course_id}/documents").json()
        if len(docs) == expected and all(d["status"] in ("ready", "failed") for d in docs):
            return docs
        time.sleep(0.1)
    raise TimeoutError(docs)


@pytest.fixture
def library(client, sample_pdfs):
    course = client.post("/api/courses", json={"name": "Machine Learning", "code": "CS229-lite"}).json()
    files = [("files", (name, sample_pdfs[name].read_bytes(), "application/pdf"))
             for name in (SVM, RNN, SLIDES, SCAN)]
    resp = client.post(f"/api/courses/{course['id']}/documents", files=files)
    assert resp.status_code == 202 and len(resp.json()["documents"]) == 4
    docs = poll_until_ready(client, course["id"], 4)
    return {"course": course, "docs": {d["pdf_name"]: d for d in docs}}


def chat(client, course_id, question, **extra):
    resp = client.post("/api/chat", json={"course_id": course_id, "question": question, **extra})
    assert resp.status_code == 200, resp.text
    events = parse_sse(resp.text)
    by_name: dict[str, list] = {}
    for name, data in events:
        by_name.setdefault(name, []).append(data)
    answer = "".join(d["text"] for d in by_name.get("token", []))
    return events, by_name, answer


def make_quiz(client, course_id, **body):
    resp = client.post("/api/quiz", json={"course_id": course_id, **body})
    assert resp.status_code == 200, resp.text
    events = parse_sse(resp.text)
    quiz_id = events[0][1]["quiz_id"]
    questions = [d["question"] for n, d in events if n == "question"]
    return quiz_id, questions, events


# --- UC1: build a document library ------------------------------------------------------


def test_uc1_upload_parse_and_preview_library(client, library):
    docs = library["docs"]
    assert all(d["status"] == "ready" for d in docs.values()), docs
    assert docs[SLIDES]["is_slides"] is True and docs[SVM]["is_slides"] is False
    assert docs[SVM]["num_pages"] == 3 and docs[SCAN]["num_pages"] == 1
    course = client.get(f"/api/courses/{library['course']['id']}").json()
    assert course["document_count"] == 4 and course["ready_count"] == 4

    md = client.get(f"/api/documents/{docs[SVM]['id']}/markdown").json()["markdown"]
    assert "### 3.2 KKT Conditions" in md
    assert "$$\n\\alpha" in md                      # equations preserved as LaTeX blocks
    assert "| Gaussian (RBF)" in md or "|Gaussian (RBF)|" in md  # table kept as GFM
    scan_md = client.get(f"/api/documents/{docs[SCAN]['id']}/markdown").json()["markdown"]
    assert "learning rate" in scan_md.lower()      # handwritten scan was OCRed

    pdf = client.get(f"/api/documents/{docs[RNN]['id']}/file")
    assert pdf.headers["content-type"] == "application/pdf" and pdf.content[:5] == b"%PDF-"


def test_uc1_rejects_bad_uploads_and_duplicates(client, library, sample_pdfs, invalid_pdfs):
    cid = library["course"]["id"]
    files = [("files", (n, invalid_pdfs[n].read_bytes(), "application/pdf"))
             for n in ("not_a_pdf.pdf", "empty.pdf")]
    files.append(("files", ("dup.pdf", sample_pdfs[SVM].read_bytes(), "application/pdf")))
    body = client.post(f"/api/courses/{cid}/documents", files=files).json()
    assert body["documents"] == []
    assert sorted(e["status"] for e in body["errors"]) == [400, 409, 415]
    # An encrypted PDF is accepted for upload but fails parsing with a clear error.
    enc = client.post(f"/api/courses/{cid}/documents",
                      files=[("files", ("enc.pdf", invalid_pdfs["encrypted.pdf"].read_bytes(), "application/pdf"))])
    docs = poll_until_ready(client, cid, 5)
    failed = next(d for d in docs if d["id"] == enc.json()["documents"][0]["id"])
    assert failed["status"] == "failed" and "password" in failed["error"]


# --- UC2-5: grounded chat ------------------------------------------------------------------


def test_uc2_grounded_answer_with_verified_page_citations(client, library):
    cid = library["course"]["id"]
    events, by_name, answer = chat(client, cid, "Why does the vanishing gradient problem happen in RNNs?")
    names = [n for n, _ in events]
    assert names.index("sources") < names.index("token") < names.index("citations") < names.index("done")
    sources = by_name["sources"][0]["sources"]
    assert sources[0]["pdf_name"] == RNN and len(sources) <= 5
    assert all(s["rerank_score"] > 0.35 for s in sources)
    assert "[Source: Lecture_07_RNNs.pdf, p." in answer
    report = by_name["citations"][0]
    assert report["valid_count"] >= 1 and report["invalid_count"] == 0
    cited = report["citations"][0]
    assert cited["document_id"] == library["docs"][RNN]["id"] and cited["bboxes"]
    assert by_name["done"][0]["not_found"] is False


def test_uc3_question_outside_notes_returns_not_found(client, library):
    events, by_name, answer = chat(client, library["course"]["id"], "Explain photosynthesis in chloroplasts")
    assert "not_found" in by_name and "token" not in by_name
    assert "Not Found in Course Notes" in by_name["not_found"][0]["message"]
    assert by_name["done"][0]["not_found"] is True


def test_uc4_follow_up_question_uses_history(client, library):
    cid = library["course"]["id"]
    _, _, first = chat(client, cid, "What does the forget gate do?")
    history = [{"role": "user", "content": "What does the forget gate do?"},
               {"role": "assistant", "content": first}]
    _, by_name, answer = chat(client, cid, "What does the forget gate decide to erase?", history=history)
    assert by_name["sources"][0]["sources"][0]["pdf_name"] == RNN and answer


def test_uc5_chat_scoped_to_selected_documents(client, library):
    cid = library["course"]["id"]
    slides_id = library["docs"][SLIDES]["id"]
    _, by_name, _ = chat(client, cid, "Bayes theorem posterior prior likelihood", document_ids=[slides_id])
    assert {s["document_id"] for s in by_name["sources"][0]["sources"]} == {slides_id}
    _, scoped, _ = chat(client, cid, "Explain the KKT conditions", document_ids=[slides_id])
    assert "not_found" in scoped


def test_uc5b_query_expansion_and_math_terms(client, library):
    cid = library["course"]["id"]
    _, by_name, _ = chat(client, cid, "What does Lemma 3.2 say?", expansion="multi_query")
    src = by_name["sources"][0]
    assert src["sources"][0]["pdf_name"] == SVM and len(src["expanded_queries"]) >= 1
    search = client.post("/api/search", json={"course_id": cid, "query": "complementary slackness α g(w) = 0"}).json()
    assert search["chunks"][0]["pdf_name"] == SVM


# --- UC6-8: quizzes, grading and adaptive practice -----------------------------------------


def test_uc6_topic_quiz_is_structured_and_grounded(client, services, library):
    cid = library["course"]["id"]
    quiz_id, questions, events = make_quiz(
        client, cid, topic="LSTM forget gate vanishing gradients", num_questions=3,
        bloom_levels=["Recall", "Conceptual", "Application"], seed=7,
    )
    assert len(questions) == 3 and events[-1][1]["generated"] == 3
    assert [n for n, _ in events].count("progress") == 3
    assert {q["bloom_level"] for q in questions} == {"Recall", "Conceptual", "Application"}
    for q in questions:
        assert len(q["options"]) == 4 and q["hint"]
        assert "is_correct" not in json.dumps(q)        # answers are not leaked before grading
        stored = QuizQuestion.model_validate(services.db.get_quiz_question(quiz_id, q["question_id"])["payload"])
        assert sum(o.is_correct for o in stored.options) == 1
        assert {o.trap_type for o in stored.options if not o.is_correct} <= {
            "sign_inversion", "boundary_violation", "terminology_swap"}
        assert all(o.misconception_explanation for o in stored.options)
        parent = services.db.get_parents([stored.parent_id])[stored.parent_id]
        flat = lambda s: " ".join(s.replace("$", " ").split()).lower()  # noqa: E731
        assert flat(stored.source_quote) in flat(parent.text)
        assert parent.page_start <= stored.source_page <= parent.page_end


def test_uc7_answering_updates_mastery_first_try_only(client, services, library):
    cid = library["course"]["id"]
    quiz_id, questions, _ = make_quiz(client, cid, num_questions=2, seed=11)
    q1, q2 = questions
    s1 = QuizQuestion.model_validate(services.db.get_quiz_question(quiz_id, q1["question_id"])["payload"])
    s2 = QuizQuestion.model_validate(services.db.get_quiz_question(quiz_id, q2["question_id"])["payload"])
    url = f"/api/quizzes/{quiz_id}/questions"

    right = client.post(f"{url}/{q1['question_id']}/answer", json={"label": s1.correct_label}).json()
    assert right["is_correct"] and right["mastery_after"] > right["mastery_before"]
    assert right["source_quote"] and right["source_page"] >= 1

    wrong_label = next(lbl for lbl in "ABCD" if lbl != s2.correct_label)
    wrong = client.post(f"{url}/{q2['question_id']}/answer", json={"label": wrong_label}).json()
    assert not wrong["is_correct"] and wrong["correct_label"] == s2.correct_label
    retry = client.post(f"{url}/{q2['question_id']}/answer", json={"label": s2.correct_label}).json()
    assert retry["is_correct"] and retry["mastery_updated"] is False

    summary = client.get(f"/api/quizzes/{quiz_id}").json()["score"]
    assert summary["answered"] == 2 and summary["correct_first_try"] == 1
    mastery = client.get(f"/api/courses/{cid}/mastery").json()
    scores = {s["parent_id"]: s for s in mastery["sections"]}
    if s1.parent_id != s2.parent_id:
        assert scores[s1.parent_id]["score"] == pytest.approx(0.625)
        assert scores[s2.parent_id]["score"] == pytest.approx(0.375)
    assert mastery["sections"] == sorted(mastery["sections"], key=lambda s: s["score"])


def test_uc8_weak_spot_quiz_targets_low_mastery_sections(client, services, library):
    cid = library["course"]["id"]
    parents = services.db.list_parents(cid)
    weak = {p.parent_id for p in parents if p.pdf_name == SLIDES}
    for p in parents:
        services.db.upsert_mastery(p.parent_id, cid, 0.05 if p.parent_id in weak else 0.97,
                                   p.parent_id not in weak)
    picked = []
    for seed in range(5):
        quiz_id, questions, _ = make_quiz(client, cid, num_questions=5, adaptive=True, seed=seed)
        for q in questions:
            row = services.db.get_quiz_question(quiz_id, q["question_id"])
            picked.append(row["parent_id"] in weak)
    share = sum(picked) / len(picked)
    assert share >= 0.6, share  # 70% of slots are mastery-weighted toward the weak slides


# --- UC9-11: maintenance and isolation ------------------------------------------------------


def test_uc9_deleting_a_document_removes_it_from_search(client, library):
    cid = library["course"]["id"]
    rnn_id = library["docs"][RNN]["id"]
    assert client.delete(f"/api/documents/{rnn_id}").status_code == 204
    _, by_name, _ = chat(client, cid, "What does the forget gate do in an LSTM?")
    sources = by_name["sources"][0]["sources"]
    assert "not_found" in by_name or all(s["document_id"] != rnn_id for s in sources)
    remaining = client.get(f"/api/courses/{cid}/documents").json()
    assert rnn_id not in {d["id"] for d in remaining}


def test_uc10_courses_are_isolated(client, library, sample_pdfs):
    other = client.post("/api/courses", json={"name": "Biology"}).json()
    _, by_name, _ = chat(client, other["id"], "What are the KKT conditions?")
    assert "not_found" in by_name
    resp = client.post(f"/api/courses/{other['id']}/documents",
                       files=[("files", (SVM, sample_pdfs[SVM].read_bytes(), "application/pdf"))])
    assert resp.status_code == 202  # the same file may live in another course


def test_uc11_delete_course_cleans_up(client, library):
    cid = library["course"]["id"]
    assert client.delete(f"/api/courses/{cid}").status_code == 204
    assert client.get(f"/api/courses/{cid}").status_code == 404
    assert client.get(f"/api/courses/{cid}/documents").status_code == 404


def test_uc12_sample_course_onboarding(client):
    body = client.post("/api/courses/sample").json()
    docs = poll_until_ready(client, body["course"]["id"], 4)
    assert all(d["status"] == "ready" for d in docs)
    _, by_name, _ = chat(client, body["course"]["id"], "What is the kernel trick?")
    assert by_name["sources"][0]["sources"][0]["pdf_name"] == SVM
