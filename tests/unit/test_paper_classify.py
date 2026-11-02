"""Question type, Bloom level and topic mapping."""

import json
from dataclasses import dataclass, field

import pytest

from course_notes_rag.generation.llm import FakeLLM, LLMError
from course_notes_rag.papers.classify import (
    QuestionClassifier, classify_bloom, classify_type, numbers_in, strip_values, topic_queries,
    topic_query,
)


@pytest.mark.parametrize("text, expected", [
    ("Define the functional margin.", "definition"),
    ("What is a kernel?", "definition"),
    ("State Mercer's theorem.", "definition"),
    ("List the gates of an LSTM cell.", "definition"),
    ("Write short notes on BPTT.", "definition"),
    ("Explain the vanishing gradient problem.", "explain_compare"),
    ("Differentiate between LSTM and GRU.", "explain_compare"),
    ("Compare bagging and boosting.", "explain_compare"),
    ("Derive the dual of the optimal margin classifier.", "derivation"),
    ("Prove that weak duality always holds.", "derivation"),
    ("Show that the margin is invariant to scaling.", "derivation"),
    ("Define the SVM and derive its dual.", "derivation"),
    ("Given P(D) = 0.01 and P(T) = 0.9, compute P(D | T).", "numerical"),
    ("Calculate the variance when p = 0.25 and n = 10.", "numerical"),
    ("How many parameters does this layer with 4 units and 8 inputs have?", "numerical"),
    ("Find the value of the gradient at x = 3 and y = 2.", "numerical"),
    ("Apply the A* search algorithm from S to G.", "algorithm"),
    ("Trace minimax on the game tree below.", "algorithm"),
    ("Simulate one epoch of the perceptron.", "algorithm"),
    ("Using the k-means algorithm cluster the points.", "algorithm"),
    ("Construct a decision tree for the data.", "algorithm"),
    ("Draw the architecture of an LSTM.", "diagram"),
    ("Explain with a neat diagram the structure of a cell.", "diagram"),
    ("Sketch the decision boundary.", "diagram"),
    ("Discuss transfer learning with a suitable figure.", "diagram"),
    ("Read this case study and advise the board.", "case_based"),
    ("A hospital collects patient records and wants a classifier. Recommend one.", "case_based"),
    ("A small company has noisy data. Advise it on a model.", "case_based"),
])
def test_question_types(text, expected):
    assert classify_type(text)[0] == expected


def test_clear_cues_are_confident():
    assert classify_type("Derive the dual form.")[1] is True
    assert classify_type("Explain the kernel trick.")[1] is True


def test_no_cue_is_not_confident_and_falls_back_on_length_and_marks():
    assert classify_type("Bayes rule") == ("definition", False)
    assert classify_type("Bayes rule in practice", marks=2) == ("definition", False)
    long = "The relation between regularization strength and generalization across many data sets in practice today"
    assert classify_type(long, marks=10) == ("explain_compare", False)


def test_a_tie_between_strong_cues_is_not_confident():
    # Both numerical and algorithm score 3: the model should settle it.
    text = "Using the ID3 algorithm compute the information gain for 4 and 6 positive examples."
    assert classify_type(text) == ("numerical", False)


def test_numbers_in_skips_names_with_digits():
    assert numbers_in("Q1 x2 uses 0.5 and 10") == ["0.5", "10"]
    assert numbers_in("no digits here") == []


@pytest.mark.parametrize("text, kind, level", [
    ("Define entropy.", "definition", "Remember"),
    ("Explain the margin.", "explain_compare", "Understand"),
    ("Compute the value.", "numerical", "Apply"),
    ("Compare LSTM and GRU.", "explain_compare", "Analyze"),
    ("Justify the choice of kernel.", "explain_compare", "Evaluate"),
    ("Design a classifier for the task.", "explain_compare", "Create"),
])
def test_bloom_levels(text, kind, level):
    assert classify_bloom(text, kind) == (level, "rules")


def test_bloom_is_at_least_apply_for_problem_solving():
    assert classify_bloom("What is the output for 3 and 4?", "numerical")[0] == "Apply"
    assert classify_bloom("What is the result?", "derivation")[0] == "Apply"


def test_evaluating_an_expression_is_not_the_evaluate_level():
    assert classify_bloom("Evaluate f(2) for the given function.", "numerical")[0] == "Analyze"


def test_a_diagram_is_at_least_understand():
    assert classify_bloom("Name and label the parts in the figure.", "diagram")[0] == "Understand"


def test_a_level_printed_on_the_paper_wins():
    assert classify_bloom("Define entropy.", "definition", "5") == ("Evaluate", "paper")


@pytest.mark.parametrize("printed", ["", "0", "7", "x"])
def test_an_unusable_printed_level_is_ignored(printed):
    assert classify_bloom("Define entropy.", "definition", printed) == ("Remember", "rules")


def test_topic_query_keeps_the_subject():
    assert topic_query("Explain with a neat diagram the structure of an LSTM cell.") == "the structure of an LSTM cell"


def test_topic_query_uses_the_shared_stem():
    assert "training data" in topic_query("Compute the mean.", "Consider the training data below.")


def test_topic_query_falls_back_when_nothing_is_left():
    assert topic_query("Define") == "Define"


def test_topic_query_drops_differentiate_between():
    assert topic_query("Differentiate between hard and soft margin.") == "hard and soft margin"


@pytest.mark.parametrize("text, expected", [
    ("the variance of a Bernoulli variable when p is 0.25", "the variance of a Bernoulli variable when p is"),
    ("Given P(D) = 0.01, P(T | D) = 0.9 and more", "Given and more"),
    ("descent on J(theta) = theta^2 from theta = 2 with rate 0.1", "descent on J(theta) = theta^2 from with rate"),
    ("A disease affects 2% of people", "A disease affects of people"),
    ("replace (w, b) by (2w, 2b)", "replace (w, b) by (2w, 2b)"),
    ("no values here", "no values here"),
])
def test_strip_values(text, expected):
    assert strip_values(text) == expected


def test_a_plain_question_needs_one_search():
    assert topic_queries("Explain the kernel trick.") == ["the kernel trick"]


def test_a_problem_is_also_searched_without_its_data_and_by_its_ask():
    queries = topic_queries("A test has P(T) = 0.9 here. Calculate P(D | T) using Bayes' theorem.")
    assert len(queries) == 3
    assert "0.9" in queries[0] and "0.9" not in queries[1]
    assert queries[2] == "P(D | T) using Bayes' theorem"


def test_topic_query_is_bounded():
    assert len(topic_query("word " * 400)) <= 500


# --- The classifier ------------------------------------------------------------------------


def q(id_, text, **extra):
    return {"id": id_, "text": text, **extra}


def llm_items(*items):
    return json.dumps({"items": [
        {"id": i, "question_type": t, "bloom_level": b} for i, t, b in items
    ]})


def test_rules_decide_clear_questions_without_the_model():
    fake = FakeLLM([])
    labels = QuestionClassifier(fake, None).classify([q("a", "Derive the dual form.")])
    assert labels["a"] == {"question_type": "derivation", "type_source": "rules",
                           "bloom_level": "Apply", "bloom_source": "rules"}
    assert fake.calls == []


def test_the_model_settles_only_unclear_questions():
    fake = FakeLLM([llm_items((1, "case_based", "Analyze"))])
    labels = QuestionClassifier(fake, None).classify([
        q("clear", "Derive the dual form."), q("unclear", "Bayes rule"),
    ])
    assert labels["unclear"] == {"question_type": "case_based", "type_source": "llm",
                                 "bloom_level": "Analyze", "bloom_source": "llm"}
    assert labels["clear"]["type_source"] == "rules"
    prompt = fake.calls[0]["messages"][-1]["content"]
    assert "Bayes rule" in prompt and "Derive the dual" not in prompt


def test_the_model_cannot_override_a_level_printed_on_the_paper():
    fake = FakeLLM([llm_items((1, "definition", "Create"))])
    labels = QuestionClassifier(fake, None).classify([q("a", "Bayes rule", bloom_hint="1")])
    assert (labels["a"]["bloom_level"], labels["a"]["bloom_source"]) == ("Remember", "paper")


def test_invalid_model_labels_are_ignored():
    fake = FakeLLM([llm_items((1, "essay", "Memorise"), (9, "numerical", "Apply"))])
    labels = QuestionClassifier(fake, None).classify([q("a", "Bayes rule")])
    assert labels["a"]["type_source"] == "rules" and labels["a"]["bloom_source"] == "rules"


@pytest.mark.parametrize("reply", ["no json at all", "{}", '{"items": ["junk"]}'])
def test_unusable_model_output_keeps_the_rule_labels(reply):
    labels = QuestionClassifier(FakeLLM([reply]), None).classify([q("a", "Bayes rule")])
    assert labels["a"]["type_source"] == "rules"


def test_model_failure_keeps_the_rule_labels():
    class Broken:
        def chat(self, *_a, **_k):
            raise LLMError("down")

    labels = QuestionClassifier(Broken(), None).classify([q("a", "Bayes rule")])
    assert labels["a"]["question_type"] == "definition"


def test_unclear_questions_go_in_batches():
    fake = FakeLLM([llm_items(), llm_items()])
    QuestionClassifier(fake, None).classify([q(str(i), f"Topic {i}") for i in range(21)])
    assert len(fake.calls) == 2


@dataclass
class Chunk:
    parent_id: str = "p1"
    section_title: str = "3.2 KKT Conditions"
    lecture_title: str = "Lecture 4"
    pdf_name: str = "L4.pdf"
    document_id: str = "d1"
    page_start: int = 2
    page_end: int = 3
    rerank_score: float = 0.81234


@dataclass
class Result:
    chunks: list = field(default_factory=list)

    @property
    def not_found(self):
        return not self.chunks


class FakeRetriever:
    def __init__(self, chunks):
        self.chunks = chunks
        self.calls = []

    def retrieve(self, query, course_id, **kwargs):
        self.calls.append((query, course_id, kwargs))
        return Result(self.chunks)


def test_topics_come_from_the_best_section():
    retriever = FakeRetriever([Chunk()])
    topics = QuestionClassifier(FakeLLM([]), retriever).map_topics(
        [q("a", "Explain the KKT conditions.")], "c1"
    )
    assert topics["a"] == {
        "parent_id": "p1", "section_title": "3.2 KKT Conditions", "lecture_title": "Lecture 4",
        "notes_pdf": "L4.pdf", "document_id": "d1", "page_start": 2, "page_end": 3, "topic_score": 0.8123,
    }
    query, course, kwargs = retriever.calls[0]
    assert query == "the KKT conditions" and course == "c1"
    assert kwargs == {"top_n": 1, "threshold": 0.2, "expansion": "none"}


def test_the_best_search_decides_the_topic():
    class ByQuery(FakeRetriever):
        def retrieve(self, query, course_id, **kwargs):
            self.calls.append(query)
            if "0.25" in query:
                return Result([Chunk(section_title="Wrong", rerank_score=0.3)])
            return Result([Chunk(section_title="Common Distributions", rerank_score=0.8)])

    retriever = ByQuery([])
    topics = QuestionClassifier(FakeLLM([]), retriever).map_topics(
        [q("a", "Calculate the variance of a Bernoulli variable when p is 0.25.")], "c1"
    )
    assert topics["a"]["section_title"] == "Common Distributions" and len(retriever.calls) == 2


def test_one_good_search_is_enough_to_avoid_a_false_gap():
    class OnlyPlain(FakeRetriever):
        def retrieve(self, query, course_id, **kwargs):
            return Result([Chunk()] if "0.25" not in query else [])

    topics = QuestionClassifier(FakeLLM([]), OnlyPlain([])).map_topics(
        [q("a", "Calculate the variance when p is 0.25.")], "c1"
    )
    assert topics["a"] is not None


def test_a_question_the_notes_do_not_cover_is_a_gap():
    topics = QuestionClassifier(FakeLLM([]), FakeRetriever([])).map_topics([q("a", "Explain photosynthesis.")], "c1")
    assert topics == {"a": None}


def test_the_stem_helps_find_the_topic():
    retriever = FakeRetriever([Chunk()])
    QuestionClassifier(FakeLLM([]), retriever).map_topics(
        [q("a", "Compute the mean.", context="Consider the KKT conditions.")], "c1"
    )
    assert "KKT" in retriever.calls[0][0]
