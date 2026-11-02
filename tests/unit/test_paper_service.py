"""The paper service, its HTTP routes and the offline model's paper prompts."""

import json
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient

from course_notes_rag.api.server import create_app
from course_notes_rag.generation.offline_llm import ExtractiveLLM
from course_notes_rag.ingestion.pipeline import UploadError
from course_notes_rag.papers import prompts
from course_notes_rag.papers.offline import offline_paper_reply
from course_notes_rag.papers.samples import SAMPLE_PAPERS, build_sample_papers
from course_notes_rag.papers.service import PaperRequestError
from course_notes_rag.storage.db import NotFoundError
from tests.conftest import SVM, wait_ready
from tests.unit.test_api import parse_sse


@pytest.fixture(scope="module")
def sample_papers(tmp_path_factory):
    return build_sample_papers(tmp_path_factory.mktemp("papers"))


@pytest.fixture
def course(services):
    return services.db.create_course("ML", "CS229")


def wait_paper(services, paper_id):
    services.papers.wait(paper_id, timeout=120)
    return services.papers.store.get_paper(paper_id)


def pdf_with(tmp_path, lines, name="custom.pdf"):
    doc = pymupdf.open()
    page = doc.new_page()
    for i, line in enumerate(lines):
        page.insert_text((60, 80 + i * 24), line, fontsize=11)
    path = tmp_path / name
    doc.save(path)
    doc.close()
    return path.read_bytes()


# --- Upload ---------------------------------------------------------------------------------------


def test_upload_reads_labels_and_finishes(services, course, sample_papers):
    data = sample_papers["End_Semester_2023.pdf"].read_bytes()
    paper = services.papers.submit_upload(course["id"], "End_Semester_2023.pdf", data)
    assert paper["status"] == "queued"
    done = wait_paper(services, paper["id"])
    assert done["status"] == "ready" and done["progress"] == 1
    assert done["question_count"] == 11 and done["engine"] == "pymupdf" and done["num_pages"] >= 1
    questions = services.papers.store.list_questions(paper_id=paper["id"])
    assert all(q["question_type"] and q["bloom_level"] for q in questions)


def test_a_paper_uploaded_before_the_notes_has_no_topics_yet(services, course, sample_papers):
    data = sample_papers["CAT1_Fall_2025.pdf"].read_bytes()
    paper = services.papers.submit_upload(course["id"], "CAT.pdf", data)
    assert wait_paper(services, paper["id"])["status"] == "ready"
    questions = services.papers.store.list_questions(paper_id=paper["id"])
    assert all(q["section_title"] is None for q in questions)


@pytest.mark.parametrize("data, status, message", [
    (b"", 400, "empty"), (b"plain text", 415, "not a PDF"),
])
def test_bad_uploads_are_refused(services, course, data, status, message):
    with pytest.raises(UploadError, match=message) as err:
        services.papers.submit_upload(course["id"], "x.pdf", data)
    assert err.value.status_code == status


def test_too_large_upload_is_refused(services, course):
    services.papers.max_bytes = 8
    with pytest.raises(UploadError, match="larger") as err:
        services.papers.submit_upload(course["id"], "x.pdf", b"%PDF-1.7 and more")
    assert err.value.status_code == 413


def test_upload_needs_a_course(services):
    with pytest.raises(NotFoundError):
        services.papers.submit_upload("missing", "x.pdf", b"%PDF-1.7")


def test_the_same_paper_twice_is_refused(services, course, sample_papers):
    data = sample_papers["CAT1_Fall_2025.pdf"].read_bytes()
    first = services.papers.submit_upload(course["id"], "CAT.pdf", data)
    wait_paper(services, first["id"])
    with pytest.raises(UploadError, match="already in this course") as err:
        services.papers.submit_upload(course["id"], "again.pdf", data)
    assert err.value.status_code == 409 and err.value.existing["id"] == first["id"]


def test_the_same_file_name_gets_a_number(services, course, sample_papers):
    a = services.papers.submit_upload(course["id"], "exam.pdf", sample_papers["CAT1_Fall_2025.pdf"].read_bytes())
    b = services.papers.submit_upload(course["id"], "exam.pdf", sample_papers["End_Semester_2023.pdf"].read_bytes())
    assert (a["pdf_name"], b["pdf_name"]) == ("exam.pdf", "exam (2).pdf")
    wait_paper(services, a["id"])
    wait_paper(services, b["id"])


def test_a_pdf_without_questions_fails_with_a_reason(services, course, tmp_path):
    data = pdf_with(tmp_path, ["These are simply some lecture notes.", "There is nothing to answer here."])
    paper = services.papers.submit_upload(course["id"], "notes.pdf", data)
    done = wait_paper(services, paper["id"])
    assert done["status"] == "failed" and "No exam questions" in done["error"]


def test_a_blank_pdf_fails_with_a_reason(services, course, invalid_pdfs):
    paper = services.papers.submit_upload(course["id"], "blank.pdf", invalid_pdfs["blank.pdf"].read_bytes())
    done = wait_paper(services, paper["id"])
    assert done["status"] == "failed" and "No text" in done["error"]


def test_a_broken_pdf_fails_with_a_reason(services, course, invalid_pdfs):
    paper = services.papers.submit_upload(course["id"], "t.pdf", invalid_pdfs["truncated.pdf"].read_bytes())
    assert wait_paper(services, paper["id"])["status"] == "failed"


def test_a_paper_deleted_while_queued_is_skipped(services, course, sample_papers):
    paper = services.papers.submit_upload(course["id"], "CAT.pdf", sample_papers["CAT1_Fall_2025.pdf"].read_bytes())
    services.papers.delete_paper(paper["id"])
    services.papers.wait(paper["id"], timeout=60)
    with pytest.raises(NotFoundError):
        services.papers.store.get_paper(paper["id"])


def test_sample_papers_can_be_added_twice_without_error(services, course, tmp_path):
    first = services.papers.add_sample_papers(course["id"], tmp_path)
    assert len(first["papers"]) == len(SAMPLE_PAPERS)
    for paper in first["papers"]:
        wait_paper(services, paper["id"])
    second = services.papers.add_sample_papers(course["id"], tmp_path)
    assert second["papers"] == [] and len(second["skipped"]) == len(SAMPLE_PAPERS)


# --- Editing -----------------------------------------------------------------------------------------


def ready_paper(services, course, sample_papers):
    paper = services.papers.submit_upload(course["id"], "CAT.pdf", sample_papers["CAT1_Fall_2025.pdf"].read_bytes())
    wait_paper(services, paper["id"])
    return paper


def test_private_fields_stay_private(services, course, sample_papers):
    paper = ready_paper(services, course, sample_papers)
    assert "file_path" not in services.papers.get_paper(paper["id"])
    assert "content_hash" not in services.papers.list_papers(course["id"])[0]


def test_title_and_year_can_be_corrected(services, course, sample_papers):
    paper = ready_paper(services, course, sample_papers)
    updated = services.papers.update_paper(paper["id"], {"title": "CAT 1", "year": 2019})
    assert (updated["title"], updated["year"]) == ("CAT 1", 2019)


def test_other_fields_cannot_be_edited(services, course, sample_papers):
    paper = ready_paper(services, course, sample_papers)
    with pytest.raises(PaperRequestError, match="cannot be edited"):
        services.papers.update_paper(paper["id"], {"status": "ready"})


def test_reprocess_runs_again(services, course, sample_papers):
    paper = ready_paper(services, course, sample_papers)
    assert services.papers.reprocess(paper["id"])["status"] == "queued"
    assert wait_paper(services, paper["id"])["question_count"] == 4


def test_reprocess_refuses_a_paper_in_progress(services, course, sample_papers):
    paper = ready_paper(services, course, sample_papers)
    services.papers.store.update_paper(paper["id"], status="parsing")
    with pytest.raises(PaperRequestError, match="still being processed"):
        services.papers.reprocess(paper["id"])


def test_delete_removes_the_file(services, course, sample_papers):
    paper = ready_paper(services, course, sample_papers)
    path, _name = services.papers.file_path(paper["id"])
    assert path.exists()
    services.papers.delete_paper(paper["id"])
    assert not path.exists()


def test_course_files_are_cleaned_up(services, course, sample_papers):
    paper = ready_paper(services, course, sample_papers)
    path, _name = services.papers.file_path(paper["id"])
    services.papers.delete_course_files(course["id"])
    assert not path.exists()
    services.papers.delete_course_files("never-existed")


def test_blueprint_is_reused_until_something_changes(services, course, sample_papers):
    paper = ready_paper(services, course, sample_papers)
    first = services.papers.blueprint(course["id"])
    assert services.papers.blueprint(course["id"]) is first
    services.papers.update_paper(paper["id"], {"title": "Renamed"})
    second = services.papers.blueprint(course["id"])
    assert second is not first and second["structures"][0]["title"] == "Renamed"


def test_plan_with_bad_days_is_a_request_error(services, course):
    with pytest.raises(PaperRequestError, match="days"):
        services.papers.plan(course["id"], 0)


def test_remap_ignores_papers_not_ready(services, course, sample_papers):
    paper = ready_paper(services, course, sample_papers)
    services.papers.store.update_paper(paper["id"], status="failed")
    assert services.papers.remap(course["id"]) == {"queued": 0}


def test_notes_added_later_are_picked_up_by_remap(services, course, sample_papers, sample_pdfs):
    paper = ready_paper(services, course, sample_papers)
    doc = services.ingestion.submit_upload(course["id"], SVM, sample_pdfs[SVM].read_bytes())
    wait_ready(services, doc["id"])
    assert services.papers.remap(course["id"]) == {"queued": 1}
    assert wait_paper(services, paper["id"])["status"] == "ready"
    topics = [q["section_title"] for q in services.papers.store.list_questions(paper_id=paper["id"])]
    assert any(topics)


def test_a_failed_remap_keeps_the_paper_usable(services, course, sample_papers):
    paper = ready_paper(services, course, sample_papers)

    def boom(*_a, **_k):
        raise RuntimeError("index offline")

    services.papers.classifier.map_topics = boom
    services.papers.remap(course["id"])
    done = wait_paper(services, paper["id"])
    assert done["status"] == "ready" and "index offline" in done["warnings"][0]


# --- HTTP --------------------------------------------------------------------------------------------


@pytest.fixture
def client(services):
    with TestClient(create_app(services)) as c:
        yield c


def upload(client, course_id, *files):
    return client.post(f"/api/courses/{course_id}/papers",
                       files=[("files", (n, d, "application/pdf")) for n, d in files])


def test_api_upload_list_detail_file_delete(client, services, course, sample_papers):
    data = sample_papers["CAT1_Fall_2025.pdf"].read_bytes()
    resp = upload(client, course["id"], ("CAT.pdf", data))
    assert resp.status_code == 202
    paper_id = resp.json()["papers"][0]["id"]
    services.papers.wait(paper_id, timeout=60)

    listed = client.get(f"/api/courses/{course['id']}/papers").json()
    assert listed[0]["status"] == "ready" and listed[0]["question_count"] == 4
    detail = client.get(f"/api/papers/{paper_id}").json()
    assert len(detail["questions"]) == 4 and detail["exam_type"] == "CAT-1"
    pdf = client.get(f"/api/papers/{paper_id}/file")
    assert pdf.headers["content-type"] == "application/pdf" and pdf.content.startswith(b"%PDF")

    assert client.delete(f"/api/papers/{paper_id}").status_code == 204
    assert client.get(f"/api/papers/{paper_id}").status_code == 404


def test_api_single_bad_upload_reports_the_error(client, course):
    resp = upload(client, course["id"], ("x.pdf", b"plain text"))
    assert resp.status_code == 415 and "not a PDF" in resp.json()["detail"]


def test_api_mixed_upload_keeps_the_good_files(client, services, course, sample_papers):
    good = sample_papers["CAT1_Fall_2025.pdf"].read_bytes()
    resp = upload(client, course["id"], ("good.pdf", good), ("bad.pdf", b"plain text"))
    body = resp.json()
    assert resp.status_code == 202 and len(body["papers"]) == 1
    assert body["errors"][0]["status"] == 415
    services.papers.wait(body["papers"][0]["id"], timeout=60)


def test_api_duplicate_upload_is_a_conflict(client, services, course, sample_papers):
    data = sample_papers["CAT1_Fall_2025.pdf"].read_bytes()
    first = upload(client, course["id"], ("a.pdf", data)).json()["papers"][0]
    services.papers.wait(first["id"], timeout=60)
    resp = upload(client, course["id"], ("b.pdf", data))
    assert resp.status_code == 409 and resp.json()["existing"]["id"] == first["id"]


@pytest.mark.parametrize("method, path", [
    ("get", "/api/courses/nope/papers"), ("get", "/api/courses/nope/papers/blueprint"),
    ("get", "/api/courses/nope/papers/plan"), ("get", "/api/courses/nope/mocks"),
    ("post", "/api/courses/nope/papers/sample"), ("post", "/api/courses/nope/papers/remap"),
    ("get", "/api/papers/nope"), ("get", "/api/papers/nope/file"), ("delete", "/api/papers/nope"),
    ("post", "/api/papers/nope/reprocess"), ("get", "/api/mocks/nope"), ("delete", "/api/mocks/nope"),
])
def test_api_unknown_ids_are_404(client, method, path):
    assert getattr(client, method)(path).status_code == 404


def test_api_missing_pdf_is_404(client, services, course, sample_papers):
    paper = ready_paper(services, course, sample_papers)
    Path(services.papers.file_path(paper["id"])[0]).unlink()
    assert client.get(f"/api/papers/{paper['id']}/file").status_code == 404


def test_api_patch_paper(client, services, course, sample_papers):
    paper = ready_paper(services, course, sample_papers)
    resp = client.patch(f"/api/papers/{paper['id']}", json={"year": 2018})
    assert resp.status_code == 200 and resp.json()["year"] == 2018
    assert client.patch("/api/papers/nope", json={"year": 2018}).status_code == 404


@pytest.mark.parametrize("body", [{"year": 1200}, {"year": 3000}, {"title": ""}, {"title": "x" * 200}])
def test_api_patch_validates(client, services, course, sample_papers, body):
    paper = ready_paper(services, course, sample_papers)
    assert client.patch(f"/api/papers/{paper['id']}", json=body).status_code == 422


def test_api_reprocess_conflict_is_a_400(client, services, course, sample_papers):
    paper = ready_paper(services, course, sample_papers)
    services.papers.store.update_paper(paper["id"], status="parsing")
    assert client.post(f"/api/papers/{paper['id']}/reprocess").status_code == 400


@pytest.mark.parametrize("days", [0, 61, "soon"])
def test_api_plan_validates_days(client, course, days):
    assert client.get(f"/api/courses/{course['id']}/papers/plan", params={"days": days}).status_code == 422


@pytest.mark.parametrize("body", [
    {"kind": "exam"}, {"num_questions": 0}, {"num_questions": 11}, {"question_types": ["essay"]},
    {"topic": "x" * 301},
])
def test_api_mock_validates(client, course, body):
    assert client.post("/api/papers/mock", json={"course_id": course["id"], **body}).status_code == 422


def test_api_mock_needs_a_known_course(client):
    assert client.post("/api/papers/mock", json={"course_id": "nope"}).status_code == 404


def test_api_mock_stream_and_delete(client, services, sample_pdfs, sample_papers):
    course = services.db.create_course("ML", "CS229")
    doc = services.ingestion.submit_upload(course["id"], SVM, sample_pdfs[SVM].read_bytes())
    wait_ready(services, doc["id"])
    paper = services.papers.submit_upload(course["id"], "E.pdf", sample_papers["End_Semester_2023.pdf"].read_bytes())
    wait_paper(services, paper["id"])
    resp = client.post("/api/papers/mock", json={
        "course_id": course["id"], "kind": "practice", "num_questions": 2, "seed": 2,
    })
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(resp.text)
    assert events[0][0] == "mock" and events[-1][0] == "done"
    mock_id = events[0][1]["mock_id"]
    assert client.delete(f"/api/mocks/{mock_id}").status_code == 204
    assert client.get(f"/api/mocks/{mock_id}").status_code == 404


# --- The offline model ---------------------------------------------------------------------------------


def test_other_prompts_are_not_handled_here():
    assert offline_paper_reply([{"role": "system", "content": "Answer the question."}]) is None
    assert offline_paper_reply([]) is None


def test_offline_classification_follows_the_contract():
    messages = prompts.classify_messages([(1, "Derive the dual."), (2, "Define entropy.")])
    items = json.loads(ExtractiveLLM().chat(messages, json_schema=prompts.classify_schema()))["items"]
    assert items == [
        {"id": 1, "question_type": "derivation", "bloom_level": "Apply"},
        {"id": 2, "question_type": "definition", "bloom_level": "Remember"},
    ]


def test_offline_extraction_reads_plain_sentences():
    messages = prompts.extract_messages("Define entropy. Some remark here. What is a kernel?")
    questions = json.loads(ExtractiveLLM().chat(messages))["questions"]
    assert [q["text"] for q in questions] == ["Define entropy.", "What is a kernel?"]


def test_offline_mock_without_notes_gives_an_invalid_reply():
    system = {"role": "system", "content": "You write one exam question"}
    assert offline_paper_reply([system, {"role": "user", "content": "no notes"}]) == "{}"
