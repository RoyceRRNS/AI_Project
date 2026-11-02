"""The past-paper tables: rows, foreign keys and the failure paths."""

import pytest

from course_notes_rag.models import ParentChunk
from course_notes_rag.storage.db import Database, NotFoundError
from course_notes_rag.storage.papers import PaperStore
from tests.conftest import block


@pytest.fixture
def db():
    database = Database(":memory:")
    yield database
    database.close()


@pytest.fixture
def store(db):
    return PaperStore(db)


@pytest.fixture
def course(db):
    return db.create_course("ML", "CS229")


def add_paper(store, course, name="a.pdf", digest="hash"):
    return store.create_paper(course["id"], name, f"/tmp/{name}", digest, 10)


def question(number="1", **extra):
    return {"number": number, "sub": "", "label": number, "part": "", "choice_group": "", "context": "",
            "text": f"Question {number}", "marks": 5.0, "marks_estimated": 0, "page": 1,
            "course_outcome": "", **extra}


def add_section(db, course, parent_id="p1"):
    doc = db.create_document(course["id"], "n.pdf", "/tmp/n.pdf", "h", 1)
    parent = ParentChunk(parent_id, doc["id"], course["id"], "n.pdf", "L1", "S1", ["S1"],
                         [block("text", "body")], 5, 0)
    db.save_chunks(doc["id"], [parent], [])
    return doc


def test_opening_twice_is_harmless(db):
    PaperStore(db)
    PaperStore(db)


def test_create_and_get_paper(store, course):
    paper = add_paper(store, course)
    assert paper["status"] == "queued" and paper["progress"] == 0
    assert paper["parts"] == [] and paper["warnings"] == []
    assert paper["question_count"] == 0 and paper["marks_found"] == 0
    assert store.get_paper(paper["id"])["pdf_name"] == "a.pdf"


def test_create_paper_needs_a_course(store):
    with pytest.raises(NotFoundError):
        store.create_paper("missing", "a.pdf", "/tmp/a.pdf", "h", 1)


def test_get_paper_missing(store):
    with pytest.raises(NotFoundError):
        store.get_paper("missing")


def test_list_papers_needs_a_course(store):
    with pytest.raises(NotFoundError):
        store.list_papers("missing")


def test_papers_list_oldest_first_with_undated_last(store, course):
    undated = add_paper(store, course, "u.pdf", "h1")
    new = add_paper(store, course, "n.pdf", "h2")
    old = add_paper(store, course, "o.pdf", "h3")
    store.update_paper(new["id"], year=2024)
    store.update_paper(old["id"], year=2020)
    assert [p["id"] for p in store.list_papers(course["id"])] == [old["id"], new["id"], undated["id"]]


def test_find_by_hash_and_name(store, course):
    paper = add_paper(store, course)
    assert store.find_paper_by_hash(course["id"], "hash")["id"] == paper["id"]
    assert store.find_paper_by_hash(course["id"], "other") is None
    assert store.find_paper_by_name(course["id"], "A.PDF")["id"] == paper["id"]
    assert store.find_paper_by_name(course["id"], "b.pdf") is None


def test_update_paper_round_trips_json_fields(store, course):
    paper = add_paper(store, course)
    store.update_paper(paper["id"], parts=[{"name": "A"}], warnings=["w"], status="ready", year=2024)
    saved = store.get_paper(paper["id"])
    assert saved["parts"] == [{"name": "A"}] and saved["warnings"] == ["w"]
    assert (saved["status"], saved["year"]) == ("ready", 2024)


def test_update_paper_rejects_unknown_fields(store, course):
    with pytest.raises(ValueError, match="Unknown paper fields"):
        store.update_paper(add_paper(store, course)["id"], course_id="x")


def test_update_paper_missing(store):
    with pytest.raises(NotFoundError):
        store.update_paper("missing", status="ready")


def test_update_paper_with_nothing_does_nothing(store, course):
    store.update_paper(add_paper(store, course)["id"])


def test_delete_paper_removes_its_questions(store, course):
    paper = add_paper(store, course)
    store.save_questions(paper["id"], course["id"], [question()])
    store.delete_paper(paper["id"])
    assert store.list_questions(course_id=course["id"]) == []
    with pytest.raises(NotFoundError):
        store.delete_paper(paper["id"])


def test_saving_questions_replaces_the_old_ones(store, course):
    paper = add_paper(store, course)
    store.save_questions(paper["id"], course["id"], [question("1"), question("2")])
    ids = store.save_questions(paper["id"], course["id"], [question("3")])
    saved = store.list_questions(paper_id=paper["id"])
    assert [q["id"] for q in saved] == ids and saved[0]["number"] == "3"
    summary = store.get_paper(paper["id"])
    assert (summary["question_count"], summary["marks_found"]) == (1, 5)


def test_list_questions_needs_exactly_one_scope(store):
    with pytest.raises(ValueError):
        store.list_questions()
    with pytest.raises(ValueError):
        store.list_questions(paper_id="a", course_id="b")


def test_ready_only_hides_papers_still_processing(store, course):
    paper = add_paper(store, course)
    store.save_questions(paper["id"], course["id"], [question()])
    assert store.list_questions(course_id=course["id"], ready_only=True) == []
    store.update_paper(paper["id"], status="ready")
    assert len(store.list_questions(course_id=course["id"], ready_only=True)) == 1


def test_update_classification(store, course):
    paper = add_paper(store, course)
    qid = store.save_questions(paper["id"], course["id"], [question()])[0]
    store.update_classification({qid: {"question_type": "numerical", "type_source": "llm"}})
    saved = store.list_questions(paper_id=paper["id"])[0]
    assert (saved["question_type"], saved["type_source"]) == ("numerical", "llm")


def test_update_classification_rejects_unknown_fields(store, course):
    paper = add_paper(store, course)
    qid = store.save_questions(paper["id"], course["id"], [question()])[0]
    with pytest.raises(ValueError, match="Unknown classification"):
        store.update_classification({qid: {"text": "changed"}})


def test_update_topics_and_clear_them(db, store, course):
    add_section(db, course)
    paper = add_paper(store, course)
    qid = store.save_questions(paper["id"], course["id"], [question()])[0]
    store.update_topics({qid: {"parent_id": "p1", "section_title": "S1", "topic_score": 0.9}})
    assert store.list_questions(paper_id=paper["id"])[0]["parent_id"] == "p1"
    store.update_topics({qid: None})
    saved = store.list_questions(paper_id=paper["id"])[0]
    assert saved["parent_id"] is None and saved["section_title"] is None


def test_a_section_deleted_while_mapping_clears_the_topic(store, course):
    paper = add_paper(store, course)
    qid = store.save_questions(paper["id"], course["id"], [question()])[0]
    store.update_topics({qid: {"parent_id": "gone", "section_title": "S1"}})
    assert store.list_questions(paper_id=paper["id"])[0]["section_title"] is None


def test_deleting_notes_keeps_the_title_but_clears_the_link(db, store, course):
    doc = add_section(db, course)
    paper = add_paper(store, course)
    qid = store.save_questions(paper["id"], course["id"], [question()])[0]
    store.update_topics({qid: {"parent_id": "p1", "section_title": "S1"}})
    db.delete_document(doc["id"])
    saved = store.list_questions(paper_id=paper["id"])[0]
    assert saved["parent_id"] is None and saved["section_title"] == "S1", "this is how staleness shows"


def test_deleting_a_course_removes_everything(db, store, course):
    paper = add_paper(store, course)
    store.save_questions(paper["id"], course["id"], [question()])
    mock_id = store.create_mock(course["id"], "paper", "Mock", {})
    db.delete_course(course["id"])
    with pytest.raises(NotFoundError):
        store.get_paper(paper["id"])
    with pytest.raises(NotFoundError):
        store.get_mock(mock_id)


def test_mock_round_trip(store, course):
    mock_id = store.create_mock(course["id"], "practice", "Set", {"topic": "SVM"})
    store.add_mock_question(mock_id, 2, {"question": "second"})
    store.add_mock_question(mock_id, 1, {"question": "first"})
    mock = store.get_mock(mock_id)
    assert mock["spec"] == {"topic": "SVM"}
    assert [q["question"] for q in mock["questions"]] == ["first", "second"]
    listed = store.list_mocks(course["id"])[0]
    assert (listed["title"], listed["kind"], listed["question_count"]) == ("Set", "practice", 2)


def test_create_mock_needs_a_course(store):
    with pytest.raises(NotFoundError):
        store.create_mock("missing", "paper", "Mock", {})


def test_delete_mock(store, course):
    mock_id = store.create_mock(course["id"], "paper", "Mock", {})
    store.delete_mock(mock_id)
    assert store.list_mocks(course["id"]) == []
    with pytest.raises(NotFoundError):
        store.delete_mock(mock_id)
