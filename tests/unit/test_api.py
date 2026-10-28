"""Endpoint-level positive and negative cases."""

import json

import pytest
from fastapi.testclient import TestClient

from course_notes_rag.api.server import create_app
from tests.conftest import SVM, wait_ready


def parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.strip().split("\n\n"):
        name, data = None, None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if name:
            events.append((name, data))
    return events


@pytest.fixture
def client(services):
    with TestClient(create_app(services)) as c:
        yield c


@pytest.fixture
def course(client):
    return client.post("/api/courses", json={"name": "ML", "code": "CS229"}).json()


def upload(client, course_id, name, data):
    return client.post(f"/api/courses/{course_id}/documents",
                       files=[("files", (name, data, "application/pdf"))])


def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok" and body["profile"] == "test"
    assert body["llm"]["ok"] is True
    assert body["retrieval"]["rerank_threshold"] == 0.35


def test_course_endpoints(client):
    created = client.post("/api/courses", json={"name": "  Algorithms  "})
    assert created.status_code == 201 and created.json()["name"] == "Algorithms"
    cid = created.json()["id"]
    assert [c["id"] for c in client.get("/api/courses").json()] == [cid]
    assert client.get(f"/api/courses/{cid}").json()["document_count"] == 0
    assert client.delete(f"/api/courses/{cid}").status_code == 204
    assert client.get(f"/api/courses/{cid}").status_code == 404


@pytest.mark.parametrize("body", [{"name": ""}, {"name": "   "}, {}, {"name": "x" * 121}])
def test_course_validation(client, body):
    assert client.post("/api/courses", json=body).status_code == 422


def test_unknown_ids_return_404(client):
    for url in ["/api/courses/nope", "/api/courses/nope/documents", "/api/documents/nope",
                "/api/documents/nope/file", "/api/documents/nope/markdown", "/api/quizzes/nope",
                "/api/courses/nope/mastery", "/api/courses/nope/quizzes"]:
        assert client.get(url).status_code == 404, url
    assert client.delete("/api/documents/nope").status_code == 404
    assert client.delete("/api/courses/nope").status_code == 404


def test_upload_and_document_endpoints(client, services, course, sample_pdfs):
    resp = upload(client, course["id"], SVM, sample_pdfs[SVM].read_bytes())
    assert resp.status_code == 202
    doc = resp.json()["documents"][0]
    wait_ready(services, doc["id"])
    listed = client.get(f"/api/courses/{course['id']}/documents").json()
    assert listed[0]["status"] == "ready" and "markdown" not in listed[0]
    pdf = client.get(f"/api/documents/{doc['id']}/file")
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF")
    md = client.get(f"/api/documents/{doc['id']}/markdown").json()
    assert "# Lecture 4" in md["markdown"] and md["engine"] == "pymupdf"
    again = client.post(f"/api/documents/{doc['id']}/reprocess")
    assert again.status_code == 202
    wait_ready(services, doc["id"])
    assert client.delete(f"/api/documents/{doc['id']}").status_code == 204
    assert client.get(f"/api/documents/{doc['id']}").status_code == 404


def test_upload_errors(client, course, sample_pdfs, invalid_pdfs):
    assert upload(client, course["id"], "x.pdf", b"").status_code == 400
    assert upload(client, course["id"], "x.pdf", invalid_pdfs["not_a_pdf.pdf"].read_bytes()).status_code == 415
    data = sample_pdfs[SVM].read_bytes()
    assert upload(client, course["id"], SVM, data).status_code == 202
    dup = upload(client, course["id"], "again.pdf", data)
    assert dup.status_code == 409 and dup.json()["existing"]["pdf_name"] == SVM
    assert client.post(f"/api/courses/{course['id']}/documents").status_code == 422
    assert upload(client, "missing", SVM, data).status_code == 404


def test_multi_upload_reports_partial_errors(client, course, sample_pdfs, invalid_pdfs):
    resp = client.post(f"/api/courses/{course['id']}/documents", files=[
        ("files", (SVM, sample_pdfs[SVM].read_bytes(), "application/pdf")),
        ("files", ("bad.pdf", invalid_pdfs["not_a_pdf.pdf"].read_bytes(), "application/pdf")),
    ])
    body = resp.json()
    assert resp.status_code == 202 and len(body["documents"]) == 1
    assert body["errors"][0]["filename"] == "bad.pdf" and body["errors"][0]["status"] == 415


def test_markdown_conflict_while_queued(client, services, course):
    doc = services.db.create_document(course["id"], "q.pdf", "nowhere.pdf", "h", 1)
    assert client.get(f"/api/documents/{doc['id']}/markdown").status_code == 409
    assert client.post(f"/api/documents/{doc['id']}/reprocess").status_code == 409
    assert client.get(f"/api/documents/{doc['id']}/file").status_code == 404


def test_chat_stream(client, ready_course):
    cid = ready_course["course"]["id"]
    resp = client.post("/api/chat", json={"course_id": cid, "question": "What does the forget gate do?"})
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(resp.text)
    names = [n for n, _ in events]
    assert names[0] == "status" and "sources" in names and names[-1] == "done"
    assert "token" in names
    citations = dict(events)["citations"]
    assert citations["valid_count"] >= 1 and citations["invalid_count"] == 0


@pytest.mark.parametrize(
    "body",
    [{"question": ""}, {"question": "   "}, {"question": "x" * 2001}, {},
     {"question": "q", "expansion": "bogus"}, {"question": "q", "history": [{"role": "system", "content": "x"}]}],
)
def test_chat_validation(client, ready_course, body):
    body = {"course_id": ready_course["course"]["id"], **body}
    assert client.post("/api/chat", json=body).status_code == 422


def test_chat_unknown_course(client):
    assert client.post("/api/chat", json={"course_id": "nope", "question": "hi"}).status_code == 404


def test_search_endpoint(client, ready_course):
    cid = ready_course["course"]["id"]
    body = client.post("/api/search", json={"course_id": cid, "query": "kernel trick Mercer"}).json()
    assert body["not_found"] is False and body["chunks"][0]["pdf_name"] == SVM
    assert client.post("/api/search", json={"course_id": cid, "query": ""}).status_code == 422
    assert client.post("/api/search", json={"course_id": "nope", "query": "x"}).status_code == 404


def test_quiz_flow_endpoints(client, ready_course):
    cid = ready_course["course"]["id"]
    resp = client.post("/api/quiz", json={"course_id": cid, "num_questions": 2, "seed": 4})
    events = parse_sse(resp.text)
    quiz_id = events[0][1]["quiz_id"]
    questions = [d["question"] for n, d in events if n == "question"]
    assert len(questions) == 2
    q = questions[0]
    graded = client.post(f"/api/quizzes/{quiz_id}/questions/{q['question_id']}/answer", json={"label": "A"})
    assert graded.status_code == 200 and graded.json()["correct_label"] in "ABCD"
    summary = client.get(f"/api/quizzes/{quiz_id}").json()
    assert summary["score"]["answered"] == 1 and summary["score"]["total"] == 2
    assert client.get(f"/api/courses/{cid}/quizzes").json()[0]["id"] == quiz_id
    mastery = client.get(f"/api/courses/{cid}/mastery").json()
    assert mastery["attempted_sections"] == 1 and mastery["total_sections"] >= 3


@pytest.mark.parametrize(
    "body",
    [{"num_questions": 0}, {"num_questions": 11}, {"bloom_levels": []}, {"bloom_levels": ["Evaluate"]},
     {"topic": "x" * 301}],
)
def test_quiz_validation(client, ready_course, body):
    body = {"course_id": ready_course["course"]["id"], **body}
    assert client.post("/api/quiz", json=body).status_code == 422


def test_quiz_business_errors(client, ready_course):
    cid = ready_course["course"]["id"]
    off_topic = client.post("/api/quiz", json={"course_id": cid, "topic": "medieval castle architecture"})
    assert off_topic.status_code == 400 and "not found" in off_topic.json()["detail"]
    assert client.post("/api/quiz", json={"course_id": "nope"}).status_code == 404
    empty = client.post("/api/courses", json={"name": "Empty"}).json()
    assert client.post("/api/quiz", json={"course_id": empty["id"]}).status_code == 400


def test_answer_validation(client, ready_course):
    cid = ready_course["course"]["id"]
    events = parse_sse(client.post("/api/quiz", json={"course_id": cid, "num_questions": 1}).text)
    quiz_id = events[0][1]["quiz_id"]
    assert client.post(f"/api/quizzes/{quiz_id}/questions/1/answer", json={"label": "E"}).status_code == 422
    assert client.post(f"/api/quizzes/{quiz_id}/questions/9/answer", json={"label": "A"}).status_code == 404
    assert client.post("/api/quizzes/nope/questions/1/answer", json={"label": "A"}).status_code == 404


def test_sample_course_endpoint(client, services):
    body = client.post("/api/courses/sample").json()
    assert body["course"]["code"] == "CS229-lite" and len(body["documents"]) == 4
    for doc in body["documents"]:
        assert wait_ready(services, doc["id"])["status"] == "ready"


def test_spa_is_served_when_built(services, tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    services.settings.frontend_dist = dist
    with TestClient(create_app(services)) as c:
        assert c.get("/").text == "<html>app</html>"
        assert c.get("/library/anything").text == "<html>app</html>"
        assert c.get("/assets/app.js").text == "console.log(1)"
        assert c.get("/api/unknown").status_code == 404
