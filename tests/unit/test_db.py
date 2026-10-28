import sqlite3

import pytest

from course_notes_rag.ingestion.chunker import HierarchicalChunker
from course_notes_rag.storage.db import Database, NotFoundError
from tests.conftest import block, make_doc


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "meta.sqlite")
    yield database
    database.close()


def _chunks(db, course_id, doc_id):
    doc = make_doc([
        block("heading", "Section A", level=2),
        block("text", "Alpha beta gamma delta. " * 30),
        block("heading", "Section B", page=2, level=2),
        block("text", "Epsilon zeta eta theta. " * 30, page=2),
    ])
    return HierarchicalChunker(parent_min_tokens=10, parent_target_tokens=10).chunk(
        doc, document_id=doc_id, course_id=course_id, course_name="T101"
    )


def test_course_crud(db):
    course = db.create_course("  Machine Learning ", "CS229")
    assert course["name"] == "Machine Learning" and course["code"] == "CS229"
    assert course["document_count"] == 0
    assert [c["id"] for c in db.list_courses()] == [course["id"]]
    db.delete_course(course["id"])
    assert db.list_courses() == []


def test_course_validation_and_missing(db):
    with pytest.raises(ValueError):
        db.create_course("   ")
    with pytest.raises(NotFoundError):
        db.get_course("missing")
    with pytest.raises(NotFoundError):
        db.delete_course("missing")


def test_document_lifecycle(db):
    course = db.create_course("C")
    doc = db.create_document(course["id"], "Lec.pdf", "/tmp/lec.pdf", "abc", 10)
    assert doc["status"] == "queued" and doc["progress"] == 0
    db.update_document(doc["id"], status="ready", progress=1.0, page_sizes=[(595, 842)],
                       is_slides=True, markdown="# Hi")
    got = db.get_document(doc["id"], with_markdown=True)
    assert got["status"] == "ready" and got["page_sizes"] == [[595, 842]] and got["is_slides"] is True
    assert got["markdown"] == "# Hi"
    assert "markdown" not in db.get_document(doc["id"])
    assert db.find_document_by_hash(course["id"], "abc")["id"] == doc["id"]
    assert db.find_document_by_hash(course["id"], "zzz") is None
    assert db.find_document_by_name(course["id"], "lec.PDF")["id"] == doc["id"]
    assert db.get_course(course["id"])["ready_count"] == 1


def test_document_errors(db):
    course = db.create_course("C")
    with pytest.raises(NotFoundError):
        db.create_document("missing", "a.pdf", "p", "h", 1)
    doc = db.create_document(course["id"], "a.pdf", "p", "h", 1)
    with pytest.raises(ValueError):
        db.update_document(doc["id"], bogus=1)
    with pytest.raises(NotFoundError):
        db.update_document("missing", status="ready")
    with pytest.raises(NotFoundError):
        db.list_documents("missing")


def test_chunks_roundtrip_and_ready_filter(db):
    course = db.create_course("C")
    doc = db.create_document(course["id"], "a.pdf", "p", "h", 1)
    result = _chunks(db, course["id"], doc["id"])
    db.save_chunks(doc["id"], result.parents, result.children)
    # Not ready yet: hidden from search-facing listings.
    assert db.list_parents(course["id"]) == []
    assert len(db.list_parents(course["id"], ready_only=False)) == len(result.parents)
    db.update_document(doc["id"], status="ready")
    parents = db.list_parents(course["id"])
    assert [p.parent_id for p in parents] == [p.parent_id for p in result.parents]
    assert parents[0].blocks[0].bbox is not None
    children = db.get_children([c.chunk_id for c in result.children])
    assert set(children) == {c.chunk_id for c in result.children}
    assert db.get_parents([]) == {} and db.get_children([]) == {}
    # Saving again replaces rather than duplicates.
    db.save_chunks(doc["id"], result.parents, result.children)
    assert len(db.list_children(course["id"])) == len(result.children)


def test_cascade_delete_removes_chunks_and_mastery(db):
    course = db.create_course("C")
    doc = db.create_document(course["id"], "a.pdf", "p", "h", 1)
    result = _chunks(db, course["id"], doc["id"])
    db.save_chunks(doc["id"], result.parents, result.children)
    pid = result.parents[0].parent_id
    db.upsert_mastery(pid, course["id"], 0.7, True)
    db.delete_document(doc["id"])
    assert db.get_parents([pid]) == {}
    assert db.get_mastery([pid]) == {}
    with pytest.raises(NotFoundError):
        db.delete_document(doc["id"])


def test_mastery_upsert_counts(db):
    course = db.create_course("C")
    doc = db.create_document(course["id"], "a.pdf", "p", "h", 1)
    result = _chunks(db, course["id"], doc["id"])
    db.save_chunks(doc["id"], result.parents, result.children)
    db.update_document(doc["id"], status="ready")
    pid = result.parents[0].parent_id
    db.upsert_mastery(pid, course["id"], 0.625, True)
    db.upsert_mastery(pid, course["id"], 0.47, False)
    rows = {r["parent_id"]: r for r in db.course_mastery(course["id"], 0.5)}
    assert rows[pid]["attempts"] == 2 and rows[pid]["correct"] == 1
    assert rows[pid]["score"] == pytest.approx(0.47)
    other = result.parents[1].parent_id
    assert rows[other]["score"] == 0.5 and rows[other]["attempts"] == 0
    with pytest.raises(sqlite3.IntegrityError):
        db.upsert_mastery("no-such-parent", course["id"], 0.5, True)


def test_quiz_storage(db):
    course = db.create_course("C")
    quiz_id = db.create_quiz(course["id"], "Kernels", {"num_questions": 2})
    db.add_quiz_question(quiz_id, 1, None, {"q": 1})
    db.add_quiz_question(quiz_id, 2, None, {"q": 2})
    assert db.get_quiz(quiz_id)["options"] == {"num_questions": 2}
    assert [q["payload"] for q in db.get_quiz_questions(quiz_id)] == [{"q": 1}, {"q": 2}]
    db.record_attempt(quiz_id, 1, None, "A", False, 1)
    db.record_attempt(quiz_id, 1, None, "B", True, 2)
    assert db.count_attempts(quiz_id, 1) == 2 and db.count_attempts(quiz_id, 2) == 0
    assert [a["is_correct"] for a in db.list_attempts(quiz_id)] == [False, True]
    assert db.list_quizzes(course["id"])[0]["question_count"] == 2
    with pytest.raises(NotFoundError):
        db.get_quiz("missing")
    with pytest.raises(NotFoundError):
        db.get_quiz_question(quiz_id, 99)
    with pytest.raises(NotFoundError):
        db.create_quiz("missing", "t", {})
