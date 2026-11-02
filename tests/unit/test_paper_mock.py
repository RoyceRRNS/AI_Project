"""Mock papers: arithmetic checks, validation, repair, planning and the generator."""

import json
import math

import numpy as np
import pytest

from course_notes_rag.generation.llm import FakeLLM, LLMUnavailableError
from course_notes_rag.generation.offline_llm import ExtractiveLLM
from course_notes_rag.models import BBox, Block, ParentChunk
from course_notes_rag.papers import prompts
from course_notes_rag.papers.blueprint import build_blueprint
from course_notes_rag.papers.mock import (
    MockDraft, MockGenerator, MockRequest, MockRequestError, Slot, answers_agree, assign_sections,
    check_mock_draft, draft_schema, ensure_applied, finalize, practice_slots, repair_mock_draft,
    safe_eval, stated_number, suits, template_slots,
)
from course_notes_rag.storage.db import Database
from course_notes_rag.storage.papers import PaperStore

FACT = "The geometric margin equals the functional margin divided by the norm of the weight vector."
OTHER = "Gradient clipping rescales the gradient when its norm exceeds a threshold."


def parent(parent_id="s1", title="2.2 Geometric Margin", formula=True, page=2):
    blocks = [
        Block("heading", title, page, BBox(page, 50, 40, 400, 60), 2),
        Block("text", FACT, page, BBox(page, 50, 70, 500, 100)),
        Block("text", OTHER, page + 1, BBox(page + 1, 50, 70, 500, 100)),
    ]
    if formula:
        blocks.append(Block("formula", r"\gamma = \hat{\gamma} / \|w\|", page + 1, BBox(page + 1, 50, 110, 500, 130)))
    return ParentChunk(parent_id, "d1", "c1", "Lecture_04.pdf", "Lecture 4", title, [title], blocks, 80, 0)


def slot(kind="numerical", marks=10.0, label="1", **extra):
    return Slot(label=label, number=label, marks=marks, question_type=kind, **extra)


def draft(**over):
    data = {
        "question": "A classifier has functional margin 6 and weight norm 3. Compute its geometric margin.",
        "question_type": "numerical", "bloom_level": "Apply",
        "steps": [
            {"text": "The geometric margin is the functional margin divided by the norm.", "evidence_ids": [1]},
            {"text": "So $\\gamma = 6 / 3 = 2$.", "evidence_ids": []},
        ],
        "final_answer": "The geometric margin is 2.",
        "answer_expression": "6 / 3",
        "marking": [{"point": "Formula", "marks": 4}, {"point": "Result", "marks": 6}],
    }
    data.update(over)
    return MockDraft.model_validate(data)


def problems_of(d, s=None, p=None, past=(), made=()):
    return check_mock_draft(d, s or slot(), p or parent(), list(past), list(made))


# --- Arithmetic -----------------------------------------------------------------------------------


@pytest.mark.parametrize("expression, value", [
    ("6 / 3", 2), ("(3 + 4) * 0.5", 3.5), ("2 ** 3", 8), ("2 ^ 3", 8), ("sqrt(16)", 4),
    ("-1 + 2", 1), ("exp(0)", 1), ("log(e)", 1), ("7 % 4", 3), ("max(1, 2) + min(3, 4)", 5),
    ("0.01 * 0.9 / (0.01 * 0.9 + 0.99 * 0.05)", 0.15384615), ("3 × 4", 12), ("5 − 2", 3),
])
def test_safe_eval(expression, value):
    assert safe_eval(expression) == pytest.approx(value)


@pytest.mark.parametrize("expression", [
    "", "   ", "abc", "2 +", "__import__('os').system('echo hi')", "open('x')", "(1).real",
    "[1, 2]", "1 if True else 2", "lambda: 1", "2 ** 99999999", "1 / 0", "sqrt(-1)", "sqrt(4, x=1)",
    "True + 1", "'a' * 3", "x" * 400, "print(1)",
])
def test_safe_eval_refuses_anything_but_arithmetic(expression):
    assert safe_eval(expression) is None


@pytest.mark.parametrize("text, value", [
    ("The answer is 2.", 2), ("After 2 steps, x = 4.5", 4.5), ("x = -3", -3), ("It is 1/4", 0.25),
    ("about 1,250 units", 1250),
])
def test_stated_number(text, value):
    assert stated_number(text) == pytest.approx(value)


@pytest.mark.parametrize("text", ["", "no numbers here", "1/0"])
def test_stated_number_without_a_value(text):
    assert stated_number(text) is None


def test_answers_agree_allows_rounding_and_percentages():
    assert answers_agree(0.33, 1 / 3)
    assert answers_agree(95, 0.95)
    assert not answers_agree(0.5, 0.6)
    assert not answers_agree(2, 3)


# --- Validation --------------------------------------------------------------------------------


def test_a_good_draft_has_no_problems():
    assert problems_of(draft()) == []


def test_the_schema_pins_the_type():
    assert draft_schema("algorithm")["properties"]["question_type"]["enum"] == ["algorithm"]


def test_a_short_question_is_rejected():
    assert any("at least" in p for p in problems_of(draft(question="Compute 6 / 3.")))


def test_the_wrong_type_is_rejected():
    assert any("question_type" in p for p in problems_of(draft(question_type="definition")))


def test_empty_final_answer_is_rejected():
    assert any("final_answer is empty" in p for p in problems_of(draft(final_answer=""), slot("definition", 2)))


def test_broken_latex_is_rejected():
    bad = draft(steps=[{"text": "The margin is $\\frac{1}{2", "evidence_ids": [1]}])
    assert any("invalid LaTeX" in p for p in problems_of(bad))


def test_handwritten_citations_are_rejected():
    bad = draft(final_answer="It is 2. [Source: Lecture_04.pdf, p. 9]")
    assert any("evidence_ids" in p for p in problems_of(bad))


def test_numerical_needs_numbers_in_the_question():
    bad = draft(question="A classifier has some margin and some norm. Compute its geometric margin.")
    assert any("state its numbers" in p for p in problems_of(bad))


def test_numerical_needs_an_expression():
    bad = draft(answer_expression="")
    assert any("answer_expression must be plain arithmetic" in p for p in problems_of(bad))


def test_numerical_answer_must_match_the_expression():
    bad = draft(final_answer="The geometric margin is 5.")
    found = [p for p in problems_of(bad) if "recompute" in p]
    assert found and "5" in found[0] and "2" in found[0]


def test_numerical_answer_must_state_a_value():
    bad = draft(final_answer="The geometric margin is two.")
    assert any("state the computed value" in p for p in problems_of(bad))


def test_numerical_expression_must_use_the_given_numbers():
    bad = draft(answer_expression="8 / 4")
    assert any("use the numbers given" in p for p in problems_of(bad))


def test_algorithm_needs_more_than_one_step():
    bad = draft(steps=[{"text": "The geometric margin is the functional margin over the norm.", "evidence_ids": [1]}],
                question_type="algorithm")
    assert any("at least 2 solution steps" in p for p in problems_of(bad, slot("algorithm")))


def test_derivation_must_show_mathematics():
    bad = draft(question_type="derivation", steps=[
        {"text": "The geometric margin is the functional margin divided by the norm.", "evidence_ids": [1]},
        {"text": "This follows directly from the definition.", "evidence_ids": []},
    ])
    assert any("mathematics" in p for p in problems_of(bad, slot("derivation")))


def test_diagram_question_must_ask_for_a_diagram():
    bad = draft(question_type="diagram", question="Explain the geometric margin and the norm in detail.")
    assert any("draw or label" in p for p in problems_of(bad, slot("diagram")))


def test_case_based_needs_a_scenario():
    bad = draft(question_type="case_based", question="A company has data about the geometric margin.")
    assert any("scenario" in p for p in problems_of(bad, slot("case_based")))


def test_a_step_must_cite_the_notes():
    bad = draft(steps=[{"text": "So 6 / 3 = 2.", "evidence_ids": []}])
    assert any("must cite the notes" in p for p in problems_of(bad))


def test_unknown_evidence_is_rejected():
    bad = draft(steps=[{"text": "The geometric margin.", "evidence_ids": [99]}])
    assert any("do not exist" in p for p in problems_of(bad))


def test_evidence_must_relate_to_the_question():
    bad = draft(question="Photosynthesis uses light of 6 and 3 units. Compute the chlorophyll ratio.",
                final_answer="The chlorophyll ratio is 2.")
    assert any("do not support" in p for p in problems_of(bad))


def test_copying_a_past_question_is_rejected():
    original = draft().question
    assert any("copies a past question" in p for p in problems_of(draft(), past=[original]))


def test_a_new_question_in_the_same_style_is_fine():
    assert problems_of(draft(), past=["Define the geometric margin of a training set."]) == []


def test_repeating_a_question_in_the_paper_is_rejected():
    assert any("repeats" in p for p in problems_of(draft(), made=[draft().question]))


# --- Repair ------------------------------------------------------------------------------------


def test_repair_restores_latex_a_json_escape_broke():
    broken = draft(steps=[{"text": "The margin is \x0crac{1}{2} of the norm.", "evidence_ids": [1]}])
    fixed, repairs = repair_mock_draft(broken, slot(), parent())
    assert "\\frac" in fixed.steps[0].text and "text cleaned" in repairs


def test_repair_relabels_the_type_it_asked_for():
    fixed, repairs = repair_mock_draft(draft(question_type="definition"), slot(), parent())
    assert fixed.question_type == "numerical" and "question type relabelled" in repairs


def test_repair_removes_unknown_evidence():
    broken = draft(steps=[{"text": "The geometric margin is the functional margin over the norm.", "evidence_ids": [1, 99]}])
    fixed, repairs = repair_mock_draft(broken, slot(), parent())
    assert fixed.steps[0].evidence_ids == [1] and "unknown evidence removed" in repairs


def test_repair_removes_evidence_unrelated_to_its_step():
    broken = draft(steps=[{"text": "Photosynthesis needs chlorophyll and sunlight everywhere.", "evidence_ids": [1]},
                          {"text": "The geometric margin divides by the norm of the weight vector.", "evidence_ids": []}])
    fixed, repairs = repair_mock_draft(broken, slot(), parent())
    assert fixed.steps[0].evidence_ids == [] and "unrelated evidence removed" in repairs
    assert fixed.steps[1].evidence_ids == [1] and "evidence re-selected" in repairs


def test_repair_finds_evidence_the_model_left_out():
    broken = draft(steps=[{"text": "Gradient clipping rescales the gradient at a threshold.", "evidence_ids": []}])
    fixed, repairs = repair_mock_draft(broken, slot(), parent())
    assert fixed.steps[0].evidence_ids == [2] and "evidence re-selected" in repairs


def test_repair_does_not_invent_evidence():
    broken = draft(steps=[{"text": "Photosynthesis needs chlorophyll and sunlight.", "evidence_ids": []}])
    fixed, repairs = repair_mock_draft(broken, slot(), parent())
    assert fixed.steps[0].evidence_ids == [] and "evidence re-selected" not in repairs


def test_repair_adds_the_value_to_a_wordy_answer():
    fixed, repairs = repair_mock_draft(draft(final_answer="The margin is"), slot(), parent())
    assert fixed.final_answer == "The margin is 2" and "final value added" in repairs


def test_repair_scales_the_marking_scheme():
    broken = draft(marking=[{"point": "Formula", "marks": 1}, {"point": "Result", "marks": 1}])
    fixed, repairs = repair_mock_draft(broken, slot(marks=10), parent())
    assert sum(p.marks for p in fixed.marking) == 10 and "marking scaled" in repairs


def test_a_correct_marking_scheme_is_left_alone():
    fixed, repairs = repair_mock_draft(draft(), slot(marks=10), parent())
    assert [p.marks for p in fixed.marking] == [4, 6] and "marking scaled" not in repairs


def test_repair_leaves_the_original_untouched():
    original = draft(question_type="definition")
    repair_mock_draft(original, slot(), parent())
    assert original.question_type == "definition"


# --- Finalizing ------------------------------------------------------------------------------------


def test_citations_come_from_the_evidence_ids():
    result = finalize(draft(), slot(), parent(), [])
    assert "[Source: Lecture_04.pdf, p. 2]" in result["solution"]
    citation = result["report"]["citations"][0]
    assert citation["valid"] is True and citation["document_id"] == "d1" and citation["page"] == 2
    assert result["report"]["invalid_count"] == 0


def test_steps_are_numbered_and_only_cited_steps_carry_a_citation():
    lines = finalize(draft(), slot(), parent(), [])["solution"].split("\n\n")
    assert lines[0].startswith("1. ") and "[Source" in lines[0]
    assert lines[1].startswith("2. ") and "[Source" not in lines[1]


def test_a_step_citing_two_pages_gets_two_citations():
    d = draft(steps=[{"text": "The norm appears in the margin and in clipping.", "evidence_ids": [1, 2]}])
    assert finalize(d, slot(), parent(), [])["solution"].count("[Source:") == 2


def test_the_evidence_quote_is_verbatim():
    assert finalize(draft(), slot(), parent(), [])["evidence"]["quote"] == FACT


def test_numerical_questions_record_the_check():
    check = finalize(draft(), slot(), parent(), [])["answer_check"]
    assert check == {"expression": "6 / 3", "value": 2.0}


def test_other_types_have_no_answer_check():
    d = draft(question_type="definition", answer_expression="")
    assert finalize(d, slot("definition", 2), parent(), [])["answer_check"] is None


def test_the_result_carries_its_place_in_the_paper():
    result = finalize(draft(), slot(label="6(a)", part="B", choice_group="6"), parent(), ["x"])
    assert (result["label"], result["part"], result["choice_group"], result["repairs"]) == ("6(a)", "B", "6", ["x"])
    assert result["type_label"] == "Numerical / problem solving"


# --- Planning ---------------------------------------------------------------------------------------


def leaf(number, marks=2.0, kind="definition", **extra):
    return {"number": number, "label": number, "marks": marks, "question_type": kind, **extra}


def test_section_suitability():
    assert suits(parent(), "numerical")
    assert suits(parent(), "derivation")
    assert suits(parent(), "definition")
    assert not suits(parent(formula=False), "derivation")
    assert not suits(parent(formula=False), "algorithm")
    procedure = parent()
    procedure.blocks[1].text = "The SMO algorithm updates two multipliers at each step of training."
    assert suits(procedure, "algorithm")


def test_a_section_with_one_sentence_suits_nothing():
    p = parent()
    p.blocks = p.blocks[:2]
    assert not suits(p, "definition")


def test_template_keeps_the_layout():
    slots = template_slots([
        leaf("1", part="A"), leaf("2", marks=10.0, kind="numerical", part="B", sub="a", choice_group="2", parent_id="s9"),
    ], 20)
    assert [(s.label, s.part, s.marks, s.question_type) for s in slots] == [
        ("1", "A", 2, "definition"), ("2", "B", 10, "numerical"),
    ]
    assert slots[1].choice_group == "2" and slots[1].source_parent_id == "s9"


def test_template_without_marks_uses_a_sensible_default():
    assert template_slots([leaf("1", marks=None, kind="derivation")], 20)[0].marks == 10


def test_template_is_cut_at_a_whole_question():
    leaves = [leaf("1"), leaf("2", sub="a"), leaf("2", sub="b"), leaf("3")]
    assert [s.number for s in template_slots(leaves, 2)] == ["1", "2", "2"]


def test_applied_questions_are_added_when_the_template_has_none():
    slots = [slot("definition", 2, "1"), slot("explain_compare", 10, "2"), slot("explain_compare", 8, "3")]
    ensure_applied(slots, {"numerical": True, "algorithm": True})
    assert [s.question_type for s in slots] == ["definition", "numerical", "algorithm"]
    assert [s.marks for s in slots] == [2, 10, 8], "the marks pattern does not change"


def test_applied_questions_already_present_are_kept():
    slots = [slot("numerical", 5, "1"), slot("algorithm", 5, "2"), slot("explain_compare", 10, "3")]
    ensure_applied(slots, {"numerical": True, "algorithm": True})
    assert [s.question_type for s in slots] == ["numerical", "algorithm", "explain_compare"]


def test_applied_questions_need_notes_that_support_them():
    slots = [slot("explain_compare", 10, "1"), slot("explain_compare", 8, "2")]
    ensure_applied(slots, {"numerical": False, "algorithm": False})
    assert {s.question_type for s in slots} == {"explain_compare"}


def test_a_single_slot_is_not_converted():
    slots = [slot("definition", 2, "1")]
    ensure_applied(slots, {"numerical": True, "algorithm": True})
    assert slots[0].question_type == "definition"


def blueprint_with(*kinds):
    papers = [{"id": "p", "pdf_name": "p.pdf", "title": "P", "year": 2024, "parts": []}]
    questions = [
        {"id": str(i), "paper_id": "p", "number": str(i), "sub": "", "label": str(i), "part": "",
         "choice_group": "", "context": "", "text": f"Question {i}", "marks": 4.0 + i,
         "question_type": kind, "bloom_level": "Apply", "section_title": "S", "parent_id": "s1",
         "lecture_title": "L", "notes_pdf": "n.pdf", "document_id": "d", "page_start": 1, "page_end": 1, "page": 1}
        for i, kind in enumerate(kinds, start=1)
    ]
    return build_blueprint(papers, questions), questions


def test_practice_slots_lead_with_applied_questions():
    blue, questions = blueprint_with("definition", "explain_compare")
    slots = practice_slots(4, None, blue, questions)
    assert [s.question_type for s in slots][:2] == ["numerical", "algorithm"]
    assert [s.label for s in slots] == ["1", "2", "3", "4"]


def test_practice_slots_follow_the_requested_types():
    blue, questions = blueprint_with("numerical", "numerical")
    slots = practice_slots(3, ["numerical"], blue, questions)
    assert [s.question_type for s in slots] == ["numerical"] * 3
    assert slots[0].marks == 5.5, "the median of past numericals"


def test_practice_slots_default_marks_when_a_type_was_never_asked():
    blue, questions = blueprint_with("definition")
    assert practice_slots(1, ["case_based"], blue, questions)[0].marks == 10


def test_sections_are_not_repeated_while_others_fit():
    parents = [parent(f"s{i}") for i in range(3)]
    slots = [slot("definition", 2, str(i)) for i in range(3)]
    assign_sections(slots, parents, {}, np.random.default_rng(1))
    assert len({s.parents[0].parent_id for s in slots}) == 3


def test_sections_repeat_only_when_they_run_out():
    slots = [slot("definition", 2, str(i)) for i in range(3)]
    assign_sections(slots, [parent("only")], {}, np.random.default_rng(1))
    assert {s.parents[0].parent_id for s in slots} == {"only"}


def test_weightage_steers_section_choice():
    parents = [parent("heavy"), parent("light")]
    wins = 0
    for seed in range(40):
        slots = [slot("definition", 2)]
        assign_sections(slots, parents, {"heavy": 20.0, "light": 1.0}, np.random.default_rng(seed))
        wins += slots[0].parents[0].parent_id == "heavy"
    assert wins >= 30


def test_numerical_slots_go_to_sections_with_mathematics():
    plain, maths = parent("plain", formula=False), parent("maths")
    plain.blocks = [b for b in plain.blocks if "=" not in b.text]
    slots = [slot("numerical")]
    assign_sections(slots, [plain, maths], {"plain": 50.0}, np.random.default_rng(0))
    assert slots[0].parents[0].parent_id == "maths"


# --- Generator ---------------------------------------------------------------------------------------


@pytest.fixture
def setup():
    db = Database(":memory:")
    store = PaperStore(db)
    course = db.create_course("ML", "CS229")
    doc = db.create_document(course["id"], "Lecture_04.pdf", "/tmp/l.pdf", "h", 1)
    p = parent()
    p.document_id, p.course_id = doc["id"], course["id"]
    db.save_chunks(doc["id"], [p], [])
    db.update_document(doc["id"], status="ready")
    yield {"db": db, "store": store, "course": course, "parent": p}
    db.close()


def past_questions(kind="numerical"):
    return [{"id": "1", "paper_id": "p", "number": "1", "sub": "", "label": "1", "part": "",
             "choice_group": "", "context": "", "text": "Compute the margin for w = 2.", "marks": 10.0,
             "question_type": kind, "bloom_level": "Apply", "section_title": "S", "parent_id": "s1",
             "lecture_title": "L", "notes_pdf": "n.pdf", "document_id": "d", "page_start": 1,
             "page_end": 1, "page": 1}]


def papers_list():
    return [{"id": "p", "pdf_name": "p.pdf", "title": "Paper", "year": 2024, "parts": [],
             "exam_type": "FAT", "duration": "3 Hours", "max_marks": 10}]


def generate(setup, llm, retries=2, **request):
    questions = past_questions()
    blueprint = build_blueprint(papers_list(), questions)
    generator = MockGenerator(setup["db"], setup["store"], llm, None, max_retries=retries)
    req = MockRequest(course_id=setup["course"]["id"], seed=1, **request)
    return list(generator.generate(req, blueprint, questions, "CS229"))


def names(events):
    return [e.type for e in events]


GOOD = draft().model_dump_json()


def test_generator_streams_and_saves_a_paper(setup):
    events = generate(setup, FakeLLM(handler=lambda m, s: GOOD), kind="paper")
    assert names(events) == ["mock", "progress", "question", "done"]
    mock = setup["store"].get_mock(events[0].data["mock_id"])
    assert len(mock["questions"]) == 1
    assert mock["spec"]["template_title"] == "Paper" and mock["spec"]["max_marks"] == 10
    assert mock["title"] == "Mock paper in the style of Paper"


def test_practice_set_has_its_own_title(setup):
    events = generate(setup, FakeLLM(handler=lambda m, s: GOOD), kind="practice", num_questions=1,
                      question_types=["numerical"])
    assert events[0].data["title"] == "Practice set: priority topics"


def test_invalid_drafts_go_back_with_the_problems(setup):
    bad = draft(final_answer="The geometric margin is 5.").model_dump_json()
    fake = FakeLLM([bad, GOOD])
    events = generate(setup, fake, kind="paper")
    assert "question" in names(events)
    assert len(fake.calls) == 2
    feedback = fake.calls[1]["messages"][-1]["content"]
    assert "recompute" in feedback and fake.calls[1]["messages"][-2]["role"] == "assistant"


def test_malformed_json_goes_back_too(setup):
    fake = FakeLLM(["not json", GOOD])
    assert "question" in names(generate(setup, fake, kind="paper"))
    assert "problems" in fake.calls[1]["messages"][-1]["content"]


def test_a_question_that_never_validates_is_skipped_with_a_warning(setup):
    bad = draft(final_answer="The geometric margin is 5.").model_dump_json()
    events = generate(setup, FakeLLM(handler=lambda m, s: bad), retries=1, kind="paper")
    assert names(events) == ["mock", "progress", "warning", "error", "done"]
    assert "Skipped question 1" in events[2].data["message"]
    assert events[-1].data == {"mock_id": events[0].data["mock_id"], "generated": 0, "requested": 1}


def test_model_unavailable_ends_the_stream_cleanly(setup):
    events = generate(setup, FakeLLM([LLMUnavailableError("Ollama is down")]), kind="paper")
    assert names(events) == ["mock", "progress", "error", "done"]
    assert events[2].data["message"] == "Ollama is down"


def test_the_prompt_is_grounded_and_shows_real_style(setup):
    fake = FakeLLM(handler=lambda m, s: GOOD)
    generate(setup, fake, kind="paper")
    system, user = [m["content"] for m in fake.calls[0]["messages"]]
    assert 'question_type must be "numerical"' in system and "10 marks" in system
    assert "[S1]" in user and FACT in user
    assert "Compute the margin for w = 2." in user
    assert fake.calls[0]["json_schema"]["properties"]["question_type"]["enum"] == ["numerical"]


def test_offline_model_passes_every_validator(setup):
    for kind in ("definition", "explain_compare", "derivation", "numerical", "algorithm", "diagram", "case_based"):
        events = generate(setup, ExtractiveLLM(), kind="practice", num_questions=1, question_types=[kind])
        assert "question" in names(events), (kind, [e.data for e in events])


def test_refuses_a_course_with_no_past_paper(setup):
    generator = MockGenerator(setup["db"], setup["store"], FakeLLM([]), None)
    req = MockRequest(course_id=setup["course"]["id"])
    with pytest.raises(MockRequestError, match="past paper"):
        list(generator.generate(req, build_blueprint([], []), [], "CS229"))


def test_refuses_a_course_with_no_notes(setup):
    other = setup["db"].create_course("Empty")
    questions = past_questions()
    generator = MockGenerator(setup["db"], setup["store"], FakeLLM([]), None)
    with pytest.raises(MockRequestError, match="No processed notes"):
        list(generator.generate(MockRequest(course_id=other["id"]), build_blueprint(papers_list(), questions), questions, "X"))


@pytest.mark.parametrize("request_args, message", [
    ({"kind": "exam"}, "kind must be"),
    ({"kind": "practice", "num_questions": 0}, "num_questions"),
    ({"kind": "practice", "num_questions": 11}, "num_questions"),
    ({"kind": "practice", "question_types": ["essay"]}, "unknown question types"),
    ({"kind": "paper", "template_paper_id": "missing"}, "template"),
])
def test_refuses_bad_requests(setup, request_args, message):
    with pytest.raises(MockRequestError, match=message):
        generate(setup, FakeLLM([]), **request_args)


def test_topic_outside_the_notes_is_refused(setup):
    class NothingFound:
        def retrieve(self, *_a, **_k):
            class Result:
                not_found = True
                chunks = []
            return Result()

    questions = past_questions()
    generator = MockGenerator(setup["db"], setup["store"], FakeLLM([]), NothingFound())
    req = MockRequest(course_id=setup["course"]["id"], topic="photosynthesis")
    with pytest.raises(MockRequestError, match="not found in your course notes"):
        list(generator.generate(req, build_blueprint(papers_list(), questions), questions, "CS229"))


def test_marks_of_a_paper_count_one_side_of_each_or():
    from course_notes_rag.papers.mock import _paper_marks

    slots = [slot("definition", 2, "1"), Slot("2(a)", "2", 10, "numerical", sub="a", choice_group="2"),
             Slot("2(b)", "2", 10, "numerical", sub="b", choice_group="2")]
    assert _paper_marks(slots) == 12


def test_depth_guidance_follows_the_marks():
    short = prompts.mock_messages("C", "definition", 2, "n.pdf", "S", parent().blocks, [], [])[0]["content"]
    long = prompts.mock_messages("C", "derivation", 16, "n.pdf", "S", parent().blocks, [], [])[0]["content"]
    assert "1 to 2 solution steps" in short and "5 to 7 solution steps" in long


def test_scaling_keeps_a_valid_total():
    d = draft(marking=[{"point": "a", "marks": 1}, {"point": "b", "marks": 2}, {"point": "c", "marks": 4}])
    fixed, _ = repair_mock_draft(d, slot(marks=5), parent())
    assert math.isclose(sum(p.marks for p in fixed.marking), 5)


def test_json_round_trip_of_a_finished_question():
    result = finalize(draft(), slot(), parent(), [])
    assert json.loads(json.dumps(result))["question"] == draft().question
