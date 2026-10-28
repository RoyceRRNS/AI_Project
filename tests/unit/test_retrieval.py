import numpy as np
import pytest
from qdrant_client import QdrantClient

from course_notes_rag.generation.llm import FakeLLM, LLMError
from course_notes_rag.models import BBox, ChildChunk
from course_notes_rag.retrieval.encoders import HashingEmbedder, LexicalReranker
from course_notes_rag.retrieval.indexer import CourseNotesIndexer, collection_for
from course_notes_rag.retrieval.search import QueryExpander
from course_notes_rag.retrieval.sparse import SparseIndex, SparseIndexManager
from tests.conftest import RNN, SVM


def child(cid, text, course="c1", doc="d1", parent="p1", section="S"):
    return ChildChunk(cid, parent, doc, course, "L.pdf", section, text, f"[Course: X] {text}", 1, 1,
                      [BBox(1, 0, 0, 1, 1)], 10, 0)


# --- encoders -------------------------------------------------------------------


def test_hashing_embedder_is_deterministic_and_normalized():
    emb = HashingEmbedder(64)
    a = emb.encode(["support vector machine margin", "support vector machine margin"])
    assert a.shape == (2, 64)
    assert np.allclose(a[0], a[1]) and np.isclose(np.linalg.norm(a[0]), 1.0)
    assert np.allclose(emb.encode([""])[0], 0.0)
    with pytest.raises(ValueError):
        HashingEmbedder(0)


def test_hashing_embedder_similarity_tracks_overlap():
    emb = HashingEmbedder()
    q, near, far = emb.encode(["kernel trick feature map", "the kernel trick avoids the feature map",
                               "bayes theorem posterior prior"])
    assert q @ near > q @ far


def test_lexical_reranker_scores():
    r = LexicalReranker()
    assert r.score("forget gate lstm", ["The LSTM forget gate erases", "nothing here"]) == [1.0, 0.0]
    assert r.score("the of", ["anything"]) == [0.0]
    assert r.score("x", []) == []


# --- sparse -----------------------------------------------------------------------


def test_bm25_exact_terms_and_math_symbols():
    index = SparseIndex([
        child("a", "Lemma 3.2 states strong duality under Slater's condition."),
        child("b", "Gradient clipping rescales g when its norm exceeds τ."),
        child("c", "Bayes theorem relates posterior and prior.", doc="d2"),
    ])
    assert index.search("Lemma 3.2", 5)[0][0] == "a"
    assert index.search(r"what is \tau used for in clipping", 5)[0][0] == "b"
    assert [cid for cid, _ in index.search("posterior", 5, document_ids=["d1"])] == []
    assert [cid for cid, _ in index.search("posterior", 5, document_ids=["d2"])] == ["c"]


def test_bm25_negative_cases():
    index = SparseIndex([child("a", "alpha beta")])
    assert index.search("zzz unknown", 5) == []
    assert index.search("alpha", 0) == []
    assert SparseIndex([]).search("alpha", 5) == []
    assert len(SparseIndex([child("e", "")])) == 1


def test_sparse_manager_caches_until_invalidated():
    calls = []

    def load(course_id):
        calls.append(course_id)
        return [child("a", "duality gap")]

    mgr = SparseIndexManager(load)
    mgr.search("c1", "duality", 3)
    mgr.search("c1", "gap", 3)
    assert calls == ["c1"]
    mgr.invalidate("c1")
    mgr.search("c1", "gap", 3)
    assert calls == ["c1", "c1"]


# --- dense indexer ------------------------------------------------------------------


@pytest.fixture
def indexer():
    client = QdrantClient(location=":memory:")
    emb = HashingEmbedder(32)
    yield CourseNotesIndexer(client, emb, SparseIndexManager(lambda c: []), "notes")
    client.close()


def test_collection_name_includes_model():
    assert collection_for("notes", HashingEmbedder(32)) == "notes__hashing-32"


def test_index_is_idempotent_and_filtered(indexer):
    chunks = [child("11111111-1111-1111-1111-111111111111", "margin of the svm"),
              child("22222222-2222-2222-2222-222222222222", "lstm forget gate", doc="d2"),
              child("33333333-3333-3333-3333-333333333333", "other course text", course="c2")]
    assert indexer.index_chunks(chunks) == 3
    assert indexer.index_chunks(chunks) == 3  # upsert, not duplicate
    assert indexer.count("c1") == 2 and indexer.count("c2") == 1
    assert indexer.count("c1", ["d2"]) == 1
    vec = indexer.encoder.encode(["svm margin"])[0].tolist()
    hits = indexer.dense_search(vec, "c1", 10)
    assert hits[0][0] == chunks[0].chunk_id
    assert {h for h, _ in hits} <= {chunks[0].chunk_id, chunks[1].chunk_id}
    assert [h for h, _ in indexer.dense_search(vec, "c1", 10, ["d2"])] == [chunks[1].chunk_id]
    point = indexer.client.retrieve(indexer.collection, [chunks[0].chunk_id], with_payload=True)[0]
    assert set(point.payload) >= {"parent_id", "raw_text", "course_id", "pdf_name", "page_num",
                                  "section_title", "document_id"}


def test_delete_document_and_course(indexer):
    chunks = [child("11111111-1111-1111-1111-111111111111", "a"),
              child("22222222-2222-2222-2222-222222222222", "b", doc="d2")]
    indexer.index_chunks(chunks)
    indexer.delete_document("c1", "d1")
    assert indexer.count("c1") == 1
    indexer.delete_course("c1")
    assert indexer.count("c1") == 0
    assert indexer.index_chunks([]) == 0


def test_dimension_mismatch_is_detected():
    client = QdrantClient(location=":memory:")
    first = CourseNotesIndexer(client, HashingEmbedder(32), SparseIndexManager(lambda c: []), "n")
    first.index_chunks([child("11111111-1111-1111-1111-111111111111", "x")])
    other = HashingEmbedder(16)
    other.name = "hashing-32"  # same collection name, different size
    second = CourseNotesIndexer(client, other, SparseIndexManager(lambda c: []), "n")
    with pytest.raises(RuntimeError, match="dimension"):
        second.count("c1")


# --- hybrid retriever (test profile services) -----------------------------------------


def test_relevant_question_finds_the_right_lecture(services, ready_course):
    cid = ready_course["course"]["id"]
    result = services.retriever.retrieve("What are the KKT complementary slackness conditions?", cid)
    assert not result.not_found
    assert result.chunks[0].pdf_name == SVM
    assert len(result.chunks) <= 5
    assert len({c.parent_id for c in result.chunks}) == len(result.chunks)
    assert all(c.rerank_score > 0.35 for c in result.chunks)
    assert result.chunks[0].highlight_bboxes
    assert result.candidates and result.timings["total_ms"] >= 0


def test_scores_are_sorted_and_threshold_applied(services, ready_course):
    cid = ready_course["course"]["id"]
    result = services.retriever.retrieve("forget gate LSTM vanishing gradient", cid, threshold=0.0)
    scores = [c.rerank_score for c in result.candidates]
    assert scores == sorted(scores, reverse=True)
    assert result.chunks[0].pdf_name == RNN
    strict = services.retriever.retrieve("forget gate LSTM vanishing gradient", cid, threshold=1.0)
    assert strict.not_found and strict.chunks == []


def test_unrelated_question_triggers_not_found(services, ready_course):
    result = services.retriever.retrieve(
        "How do chloroplasts perform photosynthesis?", ready_course["course"]["id"]
    )
    assert result.not_found and result.chunks == []


def test_document_and_course_isolation(services, ready_course):
    cid = ready_course["course"]["id"]
    rnn_id = ready_course["docs"][RNN]["id"]
    scoped = services.retriever.retrieve("margin kernel", cid, document_ids=[rnn_id], threshold=0.0)
    assert all(c.document_id == rnn_id for c in scoped.candidates)
    other = services.db.create_course("Empty")
    assert services.retriever.retrieve("KKT conditions", other["id"]).not_found


def test_query_validation(services, ready_course):
    cid = ready_course["course"]["id"]
    with pytest.raises(ValueError):
        services.retriever.retrieve("   ", cid)
    with pytest.raises(ValueError):
        services.retriever.retrieve("kkt", cid, expansion="magic")


def test_query_expansion_modes(services, ready_course):
    cid = ready_course["course"]["id"]
    result = services.retriever.retrieve("forget gate", cid, expansion="both")
    assert len(result.queries) > 1 and not result.not_found


def test_query_expansion_failure_falls_back(services, ready_course):
    services.retriever.expander = QueryExpander(FakeLLM([LLMError("down"), LLMError("down")]))
    result = services.retriever.retrieve("forget gate", ready_course["course"]["id"], expansion="both")
    assert result.queries == ["forget gate"] and not result.not_found


def test_multi_query_parsing():
    exp = QueryExpander(FakeLLM(["1. kernel trick\n- feature maps\nkernel trick\n\nwhat is a kernel"]), 2)
    assert exp.multi_query("what is a kernel") == ["kernel trick", "feature maps"]
