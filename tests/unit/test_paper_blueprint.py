"""Blueprint aggregation and the revision plan built on it."""

import pytest

from course_notes_rag.papers.blueprint import (
    build_blueprint, content_terms, dice, find_phrasings, find_recurring, paper_structure,
    paper_weights, phrasing_of, question_shares,
)
from course_notes_rag.papers.plan import build_plan, practice_breakdown
from course_notes_rag.retrieval.encoders import HashingEmbedder


def paper(id_="p1", year=2024, **extra):
    return {"id": id_, "pdf_name": f"{id_}.pdf", "title": f"Paper {id_}", "year": year,
            "exam_type": "FAT", "max_marks": 40, "duration": "3 Hours", "parts": [], **extra}


_ids = iter(range(1, 10_000))


def q(paper_id="p1", number="1", text="Explain the margin.", marks=10.0, kind="explain_compare",
      sub="", part="", group="", section="S1", parent="s1", lecture="L1", **extra):
    label = number + "".join(f"({s})" for s in sub.split(".") if s)
    return {"id": f"q{next(_ids)}", "paper_id": paper_id, "number": number, "sub": sub, "label": label,
            "part": part, "choice_group": group, "context": "", "text": text, "marks": marks,
            "question_type": kind, "bloom_level": "Understand", "section_title": section,
            "parent_id": parent, "lecture_title": lecture if section else None,
            "notes_pdf": "L1.pdf" if section else None, "document_id": "d1" if section else None,
            "page_start": 1, "page_end": 2, "page": 1, **extra}


# --- Marks ------------------------------------------------------------------------------------


def test_sides_of_an_or_share_their_marks():
    a, b, solo = q(sub="a", group="1"), q(sub="b", group="1"), q(number="2")
    shares = question_shares([a, b, solo])
    assert (shares[a["id"]], shares[b["id"]], shares[solo["id"]]) == (0.5, 0.5, 1.0)


def test_several_leaves_on_one_side_of_an_or_keep_their_share():
    leaves = [q(sub="a.i", group="1"), q(sub="a.ii", group="1"), q(sub="b", group="1")]
    assert [question_shares(leaves)[x["id"]] for x in leaves] == [0.5, 0.5, 0.5]


def test_or_groups_with_the_same_number_in_different_papers_stay_apart():
    a, b = q("p1", sub="a", group="1"), q("p2", sub="a", group="1")
    assert question_shares([a, b]) == {a["id"]: 1.0, b["id"]: 1.0}


def test_paper_weights_favour_recent_papers():
    weights = paper_weights([paper("old", 2022), paper("new", 2024), paper("undated", None)])
    assert weights["new"] == 1.0
    assert weights["old"] == pytest.approx(0.64)
    assert weights["undated"] == pytest.approx(0.64)


def test_paper_weights_with_no_dates_at_all():
    weights = paper_weights([paper("a", None), paper("b", None)])
    assert weights["a"] == weights["b"]


# --- Structure -------------------------------------------------------------------------------


def test_structure_counts_parts_and_patterns():
    questions = [q(number=str(i), marks=2.0, part="A") for i in range(1, 4)]
    questions += [q(number="4", sub="a", marks=10.0, part="B", group="4"),
                  q(number="4", sub="b", marks=10.0, part="B", group="4")]
    parts = [{"name": "A", "instructions": "Answer ALL", "answer_count": None}]
    result = paper_structure(paper(parts=parts), questions)
    assert [p["pattern"] for p in result["parts"]] == ["3 × 2", "1 × 10"]
    assert result["parts"][0]["instructions"] == "Answer ALL"
    assert result["parts"][1]["either_or"] is True and result["parts"][1]["choice"] is False
    assert result["total_marks"] == 16


def test_structure_with_answer_any():
    questions = [q(number=str(i), marks=10.0, part="B") for i in range(1, 8)]
    parts = [{"name": "B", "instructions": "Answer any FIVE", "answer_count": 5}]
    part = paper_structure(paper(parts=parts), questions)["parts"][0]
    assert (part["questions"], part["required"], part["total"], part["choice"]) == (7, 5, 50, True)


def test_sub_questions_add_up_to_one_question():
    questions = [q(number="1", sub="a", marks=4.0), q(number="1", sub="b", marks=6.0), q(number="2", marks=10.0)]
    part = paper_structure(paper(), questions)["parts"][0]
    assert (part["questions"], part["total"], part["pattern"]) == (2, 20, "2 × 10")


def test_uneven_marks_are_described_as_a_range():
    questions = [q(number="1", marks=5.0), q(number="2", marks=10.0)]
    assert paper_structure(paper(), questions)["parts"][0]["pattern"] == "2 questions, 5 to 10 marks each"


def test_questions_without_marks_do_not_break_the_structure():
    part = paper_structure(paper(), [q(marks=None)])["parts"][0]
    assert (part["marks_each"], part["total"], part["pattern"]) == (None, 0, "1 questions")


# --- Recurrence --------------------------------------------------------------------------------


PAPERS = {"p1": paper("p1", 2023), "p2": paper("p2", 2024), "p3": paper("p3", 2025)}


def test_same_question_in_two_papers_recurs():
    found = find_recurring([q("p1", text="State Bayes' theorem."), q("p2", text="State Bayes theorem.")],
                           HashingEmbedder(), PAPERS)
    assert len(found) == 1
    assert found[0]["papers"] == 2 and found[0]["years"] == [2023, 2024]
    assert [i["year"] for i in found[0]["instances"]] == [2023, 2024]


def test_a_rephrased_question_in_the_same_section_recurs():
    found = find_recurring([
        q("p1", text="Explain with a neat diagram the structure of an LSTM cell."),
        q("p2", text="Draw the block diagram of an LSTM cell and explain each gate."),
    ], None, PAPERS)
    assert len(found) == 1


def test_different_questions_do_not_recur():
    found = find_recurring([
        q("p1", text="Explain the kernel trick.", parent="s1"),
        q("p2", text="Compute the clipped gradient norm.", parent="s2"),
    ], HashingEmbedder(), PAPERS)
    assert found == []


def test_shared_words_in_different_sections_are_not_enough():
    found = find_recurring([
        q("p1", text="Explain the gradient of the margin.", parent="s1"),
        q("p2", text="Explain the gradient of the hidden state.", parent="s2"),
    ], HashingEmbedder(), PAPERS)
    assert found == []


def test_repeats_inside_one_paper_are_not_recurrence():
    found = find_recurring([q("p1", number="1", text="State Bayes' theorem."),
                            q("p1", number="2", text="State Bayes' theorem.")], HashingEmbedder(), PAPERS)
    assert found == []


def test_recurrence_links_across_more_than_two_papers():
    found = find_recurring([q(p, text="State Bayes' theorem.") for p in ("p1", "p2", "p3")], None, PAPERS)
    assert found[0]["papers"] == 3 and len(found[0]["instances"]) == 3


def test_a_chain_of_neighbours_does_not_become_one_question():
    """Each question is close to the next, but the first and last are different questions."""
    found = find_recurring([
        q("p3", text="Define the margin of a training example."),
        q("p2", text="Define the margin of a training set with labels."),
        q("p1", text="Define the set of labels with weights."),
    ], None, PAPERS)
    assert all(c["papers"] == 2 for c in found)


def test_defining_a_term_and_proving_a_result_are_different_questions():
    found = find_recurring([
        q("p1", text="Define the functional margin of a training example.", kind="definition"),
        q("p2", text="Prove that the functional margin doubles on a training example.", kind="derivation"),
    ], None, PAPERS)
    assert found == []


def test_two_different_terms_in_one_section_are_different_questions():
    found = find_recurring([
        q("p1", text="Define the geometric margin of a training set.", kind="definition"),
        q("p2", text="Define the functional margin of a training example.", kind="definition"),
    ], None, PAPERS)
    assert found == []


def test_a_paper_counts_once_in_a_cluster():
    found = find_recurring([
        q("p1", number="1", text="State Bayes' theorem."), q("p1", number="2", text="State Bayes' theorem."),
        q("p2", text="State Bayes' theorem."),
    ], None, PAPERS)
    assert found[0]["papers"] == 2 and len(found[0]["instances"]) == 2


def test_the_newest_wording_represents_the_cluster():
    found = find_recurring([
        q("p1", text="State Bayes theorem."), q("p3", text="State Bayes' theorem now."),
    ], None, PAPERS)
    assert found[0]["text"] == "State Bayes' theorem now."


class Scorer:
    """Stands in for the cross-encoder: scores a pair by which words the two sides contain."""

    name = "scripted"

    def __init__(self, score):
        self.fixed = score
        self.calls = 0

    def score(self, query, documents):
        self.calls += 1
        return [self.fixed(query, documents[0])]


def test_the_cross_encoder_rejects_what_embeddings_cannot():
    """Real figures: these two score cosine 0.91, but 0.69 as a pair."""
    scorer = Scorer(lambda a, b: 0.69)
    found = find_recurring([
        q("p1", text="Define the geometric margin of a training example.", kind="definition"),
        q("p2", text="Define the functional margin of a training example.", kind="definition"),
    ], None, PAPERS, scorer)
    assert found == [] and scorer.calls == 2


def test_the_cross_encoder_confirms_a_rephrasing():
    found = find_recurring([
        q("p1", text="Explain with a neat diagram the structure of an LSTM cell.", kind="diagram"),
        q("p2", text="Draw the block diagram of an LSTM cell and explain each gate.", kind="diagram"),
    ], None, PAPERS, Scorer(lambda a, b: 0.73))
    assert len(found) == 1


def test_a_pair_must_agree_in_both_directions():
    found = find_recurring([
        q("p1", text="List the gates of an LSTM cell.", kind="definition"),
        q("p2", text="Draw the gates of an LSTM cell.", kind="definition"),
    ], None, PAPERS, Scorer(lambda a, b: 0.96 if a.startswith("List") else 0.5))
    assert found == []


def test_unrelated_questions_never_reach_the_cross_encoder():
    scorer = Scorer(lambda a, b: 1.0)
    found = find_recurring([
        q("p1", text="Explain the kernel trick."), q("p2", text="Compute the clipped gradient norm."),
    ], HashingEmbedder(), PAPERS, scorer)
    assert found == [] and scorer.calls == 0


def test_the_cross_encoder_links_questions_of_different_labels():
    """Classification is not perfect: a confirmed repeat counts even if the types differ."""
    found = find_recurring([
        q("p1", text="Explain the role of the learning rate in gradient descent.", kind="explain_compare"),
        q("p2", text="What is the learning rate in gradient descent?", kind="definition"),
    ], None, PAPERS, Scorer(lambda a, b: 0.99))
    assert len(found) == 1


def test_recurrence_needs_at_least_two_questions():
    assert find_recurring([q()], HashingEmbedder(), PAPERS) == []
    assert find_recurring([], HashingEmbedder(), PAPERS) == []


def test_content_terms_drop_command_words_and_numbers():
    assert content_terms("Explain the kernel trick with 2 examples") == {"kernel", "trick", "examples"}


def test_dice():
    assert dice({"a", "b"}, {"a", "b"}) == 1.0
    assert dice({"a"}, {"b"}) == 0.0
    assert dice(set(), {"a"}) == 0.0


# --- Phrasings ---------------------------------------------------------------------------------


@pytest.mark.parametrize("text, expected", [
    ("Explain with a neat diagram the cell.", "Explain … with a neat diagram"),
    ("Explain the margin with an example.", "Explain … with an example"),
    ("Explain the problem and compute the norm.", "Explain … and compute …"),
    ("Explain the margin.", "Explain …"),
    ("Differentiate between LSTM and GRU.", "Differentiate between … and …"),
    ("Compare LSTM and GRU.", "Compare … and …"),
    ("Draw the cell and label it.", "Draw … and explain …"),
    ("Derive the dual.", "Derive …"),
    ("Prove that it holds.", "Prove that …"),
    ("Trace minimax on the tree.", "Trace … step by step"),
    ("Apply two steps of descent.", "Apply … to …"),
    ("Compute the mean.", "Compute … for the given values"),
    ("Given P(D) = 0.1, compute P(T).", "Given …, compute …"),
    ("Define entropy.", "Define …"),
    ("What is a kernel?", "What is …?"),
    ("What is meant by the kernel trick?", "What is meant by …?"),
    ("State Bayes' theorem.", "State / list …"),
    ("Write short notes on BPTT.", "Write short notes on …"),
    ("Describe the model.", "Describe …"),
])
def test_phrasing(text, expected):
    assert phrasing_of(text) == expected


def test_case_based_questions_have_their_own_phrasing():
    assert phrasing_of("A hospital wants a model.", "case_based").startswith("A scenario")


def test_unknown_openings_keep_their_first_words():
    assert phrasing_of("Summarise the chapter.") == "Summarise the …"
    assert phrasing_of("") == "…"


def test_phrasings_count_and_prefer_examples_from_different_papers():
    found = find_phrasings(
        [q("p1", text="Define a."), q("p1", text="Define b."), q("p2", text="Define c."), q("p3", text="Explain d.")],
        PAPERS,
    )
    assert (found[0]["phrasing"], found[0]["count"]) == ("Define …", 3)
    assert {e["paper_id"] for e in found[0]["examples"]} == {"p1", "p2"}
    assert len(found[0]["examples"]) == 3, "room left is filled from the same papers"


# --- The whole blueprint ----------------------------------------------------------------------


def test_empty_blueprint_is_valid():
    blue = build_blueprint([], [], HashingEmbedder())
    assert blue["summary"]["papers"] == 0 and blue["typical"] is None
    assert blue["modules"] == [] and blue["sections"] == [] and blue["recurring"] == []
    assert all(t["share"] == 0 for t in blue["types"])


def test_weightage_shares_add_up_and_or_is_not_double_counted():
    questions = [
        q(number="1", sub="a", group="1", marks=10.0, section="A", parent="a"),
        q(number="1", sub="b", group="1", marks=10.0, section="B", parent="b"),
        q(number="2", marks=20.0, section="A", parent="a"),
    ]
    blue = build_blueprint([paper()], questions)
    assert blue["summary"]["marks"] == 30
    assert {s["section_title"]: s["marks"] for s in blue["sections"]} == {"A": 25, "B": 5}
    assert sum(s["share"] for s in blue["sections"]) == pytest.approx(1.0)


def test_frequency_and_marks_per_paper():
    questions = [q("p1", marks=10.0), q("p2", marks=20.0), q("p3", marks=10.0, section="Other", parent="o")]
    blue = build_blueprint([paper("p1", 2023), paper("p2", 2024), paper("p3", 2024)], questions)
    top = blue["sections"][0]
    assert (top["papers"], top["marks_per_paper"], top["years"]) == (2, 10, [2023, 2024])
    assert top["frequency"] == pytest.approx(0.6667, abs=1e-3)


def test_recent_papers_raise_expected_marks():
    old = build_blueprint([paper("p1", 2020), paper("p2", 2024)], [q("p1", marks=10.0), q("p2", marks=1.0, section="X", parent="x")])
    by_title = {s["section_title"]: s for s in old["sections"]}
    assert by_title["S1"]["marks_per_paper"] == 5 and by_title["S1"]["expected_marks"] < 5


def test_unmapped_questions_are_reported_as_gaps():
    questions = [q(marks=10.0), q(number="2", marks=10.0, section=None, parent=None)]
    blue = build_blueprint([paper()], questions)
    assert blue["summary"]["coverage"] == 0.5
    assert [g["label"] for g in blue["gaps"]] == ["2"]
    assert "Not in your notes" in {m["module"] for m in blue["modules"]}


def test_a_section_deleted_from_the_notes_is_flagged_stale():
    blue = build_blueprint([paper()], [q(parent=None)])
    assert blue["stale_questions"] == 1 and blue["gaps"] == []
    assert blue["sections"][0]["section_title"] == "S1"


def test_type_and_bloom_mix():
    blue = build_blueprint([paper()], [q(kind="numerical", marks=30.0), q(number="2", kind="definition", marks=10.0)])
    by_key = {t["key"]: t for t in blue["types"]}
    assert (by_key["numerical"]["share"], by_key["numerical"]["count"]) == (0.75, 1)
    assert by_key["numerical"]["label"] == "Numerical / problem solving"
    assert by_key["diagram"]["count"] == 0
    assert blue["practice_mix"][0]["mode"] == "numericals"
    assert next(b for b in blue["bloom"] if b["key"] == "Understand")["count"] == 2


def test_marks_distribution():
    blue = build_blueprint([paper()], [q(marks=2.0), q(number="2", marks=2.0), q(number="3", marks=10.0)])
    assert blue["marks_distribution"] == [{"marks": 2, "count": 2}, {"marks": 10, "count": 1}]


def test_questions_of_unknown_papers_are_ignored():
    blue = build_blueprint([paper("p1")], [q("p1"), q("gone")])
    assert blue["summary"]["questions"] == 1


def test_typical_structure_is_the_most_common():
    questions = [q(p, number=str(i), marks=10.0) for p in ("p1", "p2") for i in (1, 2)]
    questions += [q("p3", number="1", marks=5.0)]
    blue = build_blueprint([paper("p1", 2022), paper("p2", 2023), paper("p3", 2024)], questions)
    assert blue["typical"]["paper_id"] == "p2" and blue["typical"]["paper_count"] == 2


def test_unmarked_questions_are_counted():
    assert build_blueprint([paper()], [q(marks=None)])["summary"]["unmarked"] == 1


# --- Revision plan ----------------------------------------------------------------------------


def plan_blueprint():
    questions = [
        q("p1", marks=30.0, kind="numerical", section="Big", parent="big"),
        q("p2", number="1", marks=30.0, kind="numerical", section="Big", parent="big"),
        q("p1", number="2", marks=10.0, kind="derivation", section="Medium", parent="med"),
        q("p2", number="2", marks=2.0, kind="definition", section="Small", parent="small"),
    ]
    return build_blueprint([paper("p1", 2023), paper("p2", 2024)], questions)


def test_plan_orders_sections_by_priority():
    plan = build_plan(plan_blueprint(), {})
    assert [i["section_title"] for i in plan["items"]] == ["Big", "Medium", "Small"]
    assert [i["rank"] for i in plan["items"]] == [1, 2, 3]
    assert plan["items"][0]["priority"] == 100


def test_plan_says_what_to_practise():
    items = {i["section_title"]: i for i in build_plan(plan_blueprint(), {})["items"]}
    assert (items["Big"]["focus"], items["Big"]["focus_label"]) == ("numericals", "Numericals")
    assert items["Medium"]["focus"] == "derivations"
    assert items["Small"]["focus"] == "theory"
    assert "fresh numbers" in items["Big"]["advice"]
    assert "Asked in 2 of 2 papers" in items["Big"]["advice"]


def test_weak_sections_move_up():
    base = build_plan(plan_blueprint(), {})["items"]
    assert base[1]["section_title"] == "Medium"
    weak = build_plan(plan_blueprint(), {
        "med": {"score": 0.95, "attempts": 4}, "small": {"score": 0.05, "attempts": 4},
    })["items"]
    scores = {i["section_title"]: i for i in weak}
    assert scores["Small"]["priority"] > base[2]["priority"]
    assert scores["Medium"]["priority"] < base[1]["priority"]
    assert "quiz results here are low" in scores["Small"]["advice"]


def test_untried_sections_are_not_called_weak():
    item = build_plan(plan_blueprint(), {"big": {"score": 0.1, "attempts": 0}})["items"][0]
    assert "quiz results" not in item["advice"]


def test_tiers_follow_the_marks_covered():
    tiers = [i["tier"] for i in build_plan(plan_blueprint(), {})["items"]]
    assert tiers[0] == "must" and tiers[-1] in ("should", "optional")


def test_the_section_crossing_a_line_stays_above_it():
    """A single section holding most marks must not fall out of "must revise"."""
    assert build_plan(plan_blueprint(), {})["items"][0]["tier_label"] == "Must revise"


def test_practice_breakdown_merges_theory_types():
    rows = practice_breakdown({"definition": 2, "explain_compare": 3, "numerical": 5})
    assert {r["mode"]: r["share"] for r in rows} == {"numericals": 0.5, "theory": 0.5}


def test_practice_breakdown_of_nothing():
    assert practice_breakdown({}) == []


def test_schedule_ends_with_a_mock_paper():
    plan = build_plan(plan_blueprint(), {}, days=3)
    assert [d["day"] for d in plan["schedule"]] == [1, 2, 3]
    assert plan["schedule"][-1]["title"] == "Timed mock paper"
    planned = [s for d in plan["schedule"] for s in d["sections"]]
    assert sorted(planned) == ["Big", "Medium", "Small"], "every section gets a day"


def test_one_day_means_no_separate_mock_day():
    plan = build_plan(plan_blueprint(), {}, days=1)
    assert len(plan["schedule"]) == 1 and len(plan["schedule"][0]["sections"]) == 3


def test_more_days_than_sections_leaves_no_empty_days():
    plan = build_plan(plan_blueprint(), {}, days=30)
    assert all(d["sections"] or d["title"] == "Timed mock paper" for d in plan["schedule"])


def test_no_days_means_no_schedule():
    assert build_plan(plan_blueprint(), {})["schedule"] == []


@pytest.mark.parametrize("days", [0, -1, 61])
def test_invalid_days(days):
    with pytest.raises(ValueError, match="days"):
        build_plan(plan_blueprint(), {}, days=days)


def test_empty_plan():
    plan = build_plan(build_blueprint([], []), {}, days=3)
    assert plan["items"] == [] and plan["summary"]["papers"] == 0
    assert plan["schedule"][-1]["title"] == "Timed mock paper"
