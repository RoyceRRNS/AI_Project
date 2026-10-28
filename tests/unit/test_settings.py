import pytest
from pydantic import ValidationError

from course_notes_rag.config.settings import Settings


def test_defaults_match_the_plan(tmp_path):
    s = Settings(storage_dir=tmp_path)
    assert (s.dense_top_k, s.sparse_top_k, s.rrf_k) == (25, 25, 60)
    assert (s.fused_top_children, s.final_top_n, s.rerank_threshold) == (15, 5, 0.35)
    assert (s.parent_target_tokens, s.parent_max_tokens) == (800, 1200)
    assert (s.child_min_tokens, s.child_max_tokens, s.child_overlap_tokens) == (200, 300, 50)
    assert (s.mastery_initial, s.mastery_alpha, s.adaptive_fraction) == (0.5, 0.25, 0.7)
    assert s.embedding_model == "BAAI/bge-m3"
    assert s.reranker_model == "BAAI/bge-reranker-v2-m3"
    assert s.llm_model == "qwen2.5:7b"


def test_paths_derive_from_storage_dir(tmp_path):
    s = Settings(storage_dir=tmp_path / "data")
    assert s.sqlite_path == tmp_path / "data" / "metadata.sqlite"
    assert s.qdrant_path == tmp_path / "data" / "qdrant_db"
    s.ensure_dirs()
    assert s.uploads_dir.is_dir() and s.qdrant_path.is_dir()


def test_environment_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("CNR_RERANK_THRESHOLD", "0.5")
    monkeypatch.setenv("CNR_LLM_MODEL", "qwen2.5:14b")
    s = Settings(storage_dir=tmp_path)
    assert s.rerank_threshold == 0.5
    assert s.llm_model == "qwen2.5:14b"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"parent_min_tokens": 900, "parent_target_tokens": 800},
        {"parent_target_tokens": 1300},
        {"child_min_tokens": 400, "child_max_tokens": 300},
        {"child_overlap_tokens": 200},
        {"rerank_threshold": 1.5},
        {"dense_top_k": 0},
        {"quiz_default_questions": 20},
        {"mastery_alpha": 0},
    ],
)
def test_invalid_values_are_rejected(kwargs, tmp_path):
    with pytest.raises(ValidationError):
        Settings(storage_dir=tmp_path, **kwargs)
