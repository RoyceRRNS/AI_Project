import pytest

from course_notes_rag.ingestion.pipeline import UploadError, safe_filename
from course_notes_rag.storage.db import NotFoundError
from tests.conftest import RNN, SCAN, SVM, wait_ready


@pytest.fixture
def course(services):
    return services.db.create_course("ML", "CS229")


def test_upload_and_process_to_ready(services, course, sample_pdfs):
    doc = services.ingestion.submit_upload(course["id"], SVM, sample_pdfs[SVM].read_bytes())
    assert doc["status"] == "queued"
    done = wait_ready(services, doc["id"])
    assert done["status"] == "ready" and done["progress"] == 1.0 and done["error"] is None
    assert done["num_pages"] >= 2 and done["num_parents"] >= 1 and done["num_children"] >= done["num_parents"]
    assert done["engine"] == "pymupdf" and done["is_slides"] is False
    md = services.db.get_document(doc["id"], with_markdown=True)["markdown"]
    assert "$$" in md and "|---|" in md
    assert services.indexer.count(course["id"]) == done["num_children"]


def test_scanned_notes_are_ocred_during_ingestion(services, course, sample_pdfs):
    doc = services.ingestion.submit_upload(course["id"], SCAN, sample_pdfs[SCAN].read_bytes())
    assert wait_ready(services, doc["id"])["status"] == "ready"


@pytest.mark.parametrize(
    ("name", "status"),
    [("empty.pdf", 400), ("not_a_pdf.pdf", 415)],
)
def test_invalid_uploads(services, course, invalid_pdfs, name, status):
    with pytest.raises(UploadError) as err:
        services.ingestion.submit_upload(course["id"], name, invalid_pdfs[name].read_bytes())
    assert err.value.status_code == status


def test_too_large_upload(services, course):
    services.ingestion.max_bytes = 10
    with pytest.raises(UploadError) as err:
        services.ingestion.submit_upload(course["id"], "big.pdf", b"%PDF-1.7" + b"0" * 20)
    assert err.value.status_code == 413


def test_duplicate_content_is_rejected(services, course, sample_pdfs):
    data = sample_pdfs[RNN].read_bytes()
    first = services.ingestion.submit_upload(course["id"], RNN, data)
    with pytest.raises(UploadError) as err:
        services.ingestion.submit_upload(course["id"], "copy.pdf", data)
    assert err.value.status_code == 409 and err.value.existing["id"] == first["id"]


def test_same_name_different_content_is_renamed(services, course, sample_pdfs):
    a = services.ingestion.submit_upload(course["id"], "notes.pdf", sample_pdfs[SVM].read_bytes())
    b = services.ingestion.submit_upload(course["id"], "notes.pdf", sample_pdfs[RNN].read_bytes())
    assert (a["pdf_name"], b["pdf_name"]) == ("notes.pdf", "notes (2).pdf")


@pytest.mark.parametrize("name", ["blank.pdf", "encrypted.pdf", "truncated.pdf"])
def test_unparseable_pdfs_end_up_failed(services, course, invalid_pdfs, name):
    doc = services.ingestion.submit_upload(course["id"], name, invalid_pdfs[name].read_bytes())
    done = wait_ready(services, doc["id"])
    assert done["status"] == "failed" and done["error"]


def test_upload_to_unknown_course(services, sample_pdfs):
    with pytest.raises(NotFoundError):
        services.ingestion.submit_upload("missing", SVM, sample_pdfs[SVM].read_bytes())


def test_delete_document_removes_everything(services, course, sample_pdfs):
    doc = services.ingestion.submit_upload(course["id"], SVM, sample_pdfs[SVM].read_bytes())
    wait_ready(services, doc["id"])
    path = services.db.get_document(doc["id"])["file_path"]
    services.ingestion.delete_document(doc["id"])
    with pytest.raises(NotFoundError):
        services.db.get_document(doc["id"])
    assert services.indexer.count(course["id"]) == 0
    assert services.db.list_children(course["id"], ready_only=False) == []
    from pathlib import Path

    assert not Path(path).exists()


def test_reprocessing_does_not_duplicate_vectors(services, course, sample_pdfs):
    doc = services.ingestion.submit_upload(course["id"], RNN, sample_pdfs[RNN].read_bytes())
    first = wait_ready(services, doc["id"])
    services.ingestion.enqueue(doc["id"]).result(timeout=60)
    assert services.indexer.count(course["id"]) == first["num_children"]
    assert len(services.db.list_children(course["id"])) == first["num_children"]


def test_document_deleted_while_queued_is_skipped(services, course, sample_pdfs):
    services.ingestion.process("does-not-exist")  # no crash


def test_delete_course(services, course, sample_pdfs):
    doc = services.ingestion.submit_upload(course["id"], SVM, sample_pdfs[SVM].read_bytes())
    wait_ready(services, doc["id"])
    services.ingestion.delete_course(course["id"])
    with pytest.raises(NotFoundError):
        services.db.get_course(course["id"])
    assert services.indexer.count(course["id"]) == 0


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("Lecture 4.pdf", "Lecture 4.pdf"), ("../../etc/passwd", "passwd.pdf"),
     ("we<ird>:name?.pdf", "we_ird_name_.pdf"), ("", "document.pdf"), ("notes", "notes.pdf")],
)
def test_safe_filename(raw, expected):
    assert safe_filename(raw) == expected
