import pytest

from course_notes_rag.retrieval.fusion import reciprocal_rank_fusion
from course_notes_rag.retrieval.lexical import tokenize


def test_greek_unicode_and_latex_map_to_the_same_token():
    assert "alpha" in tokenize("α_i ≥ 0")
    assert "alpha" in tokenize(r"$\alpha_i \geq 0$")
    assert "geq" in tokenize("α ≥ 0") and "geq" in tokenize(r"\geq")
    assert tokenize("∇ f") == tokenize(r"\nabla f")


def test_theorem_numbers_and_subscripts_are_kept():
    toks = tokenize("Lemma 3.2 bounds x_i and W^T")
    assert "lemma" in toks and "3.2" in toks
    assert "x_i" in toks and "x" in toks and "w^t" in toks


def test_stopwords_removed_and_possessive():
    toks = tokenize("The margin of the classifier is Slater's condition")
    assert "the" not in toks and "is" not in toks
    assert "slater" in toks and "margin" in toks


def test_tokenize_empty():
    assert tokenize("") == [] and tokenize("the of and") == []


def test_rrf_formula_and_order():
    fused = reciprocal_rank_fusion([["a", "b", "c"], ["b", "a", "d"]], k=60)
    scores = dict(fused)
    assert scores["a"] == pytest.approx(1 / 61 + 1 / 62)
    assert scores["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert scores["c"] == pytest.approx(1 / 63)
    assert scores["d"] == pytest.approx(1 / 63)
    # Ties keep first-seen order: a before b, c before d.
    assert [d for d, _ in fused] == ["a", "b", "c", "d"]


def test_rrf_document_in_both_lists_beats_single_list_top():
    fused = reciprocal_rank_fusion([["x", "shared"], ["shared", "y"]])
    assert fused[0][0] == "shared"


def test_rrf_duplicates_in_one_ranking_count_once():
    fused = dict(reciprocal_rank_fusion([["a", "a", "b"]]))
    assert fused["a"] == pytest.approx(1 / 61)
    assert fused["b"] == pytest.approx(1 / 63)


def test_rrf_edge_cases():
    assert reciprocal_rank_fusion([]) == []
    assert reciprocal_rank_fusion([[], []]) == []
    with pytest.raises(ValueError):
        reciprocal_rank_fusion([["a"]], k=0)
