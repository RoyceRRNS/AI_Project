from course_notes_rag.text_utils import (
    approx_token_count, find_verbatim, linearize_table, linearize_tables_in, normalize_for_match,
    split_sentences, tail_words,
)


def test_split_sentences_basic():
    assert split_sentences("First one. Second one! Third?") == ["First one.", "Second one!", "Third?"]


def test_split_sentences_protects_math_and_abbreviations():
    text = "We minimize $f(x) = 1. 5x$ here. See Eq. 3 for details, e.g. the bound. Next sentence."
    out = split_sentences(text)
    assert out == [
        "We minimize $f(x) = 1. 5x$ here.",
        "See Eq. 3 for details, e.g. the bound.",
        "Next sentence.",
    ]


def test_split_sentences_empty():
    assert split_sentences("") == [] and split_sentences("   ") == []


def test_approx_token_count():
    assert approx_token_count("Hello, world!") == 4
    assert approx_token_count("") == 0


def test_tail_words_respects_budget():
    text = "one two three four five six seven eight"
    tail = tail_words(text, 3, approx_token_count)
    assert tail == "six seven eight"
    assert tail_words(text, 0, approx_token_count) == ""
    assert tail_words("", 5, approx_token_count) == ""


def test_normalize_maps_back_to_original():
    norm, index = normalize_for_match("  “Hello”\n\n  **World** ")
    assert norm == '"hello" world'
    assert len(norm) == len(index)


def test_find_verbatim_is_case_whitespace_and_quote_insensitive():
    text = "The   forget gate decides what to ERASE\nfrom the cell state."
    span = find_verbatim("the forget gate decides what to erase from the cell state", text)
    assert span is not None
    assert text[span[0] : span[1]].startswith("The   forget")
    assert text[span[0] : span[1]].endswith("cell state")


def test_linearize_table():
    md = "| Distribution | Mean | Variance |\n|---|---|---|\n| Bernoulli | p | p(1 − p) |\n| Poisson | λ | λ |"
    assert linearize_table(md) == (
        "Distribution: Bernoulli; Mean: p; Variance: p(1 − p).\n"
        "Distribution: Poisson; Mean: λ; Variance: λ."
    )
    assert linearize_table("not a table") == "not a table"
    assert linearize_table("| only header |\n|---|") == "| only header |\n|---|"


def test_linearize_tables_in_text_keeps_prose():
    text = "Intro sentence.\n\n|a|b|\n|---|---|\n|1|2|\n\nOutro."
    assert linearize_tables_in(text) == "Intro sentence.\n\na: 1; b: 2.\n\nOutro."


def test_find_verbatim_negative():
    assert find_verbatim("not in the text", "The forget gate.") is None
    assert find_verbatim("", "anything") is None
    assert find_verbatim('""', "anything") is None
