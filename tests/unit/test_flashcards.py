import json
from datetime import datetime, timedelta, timezone

import pytest

from course_notes_rag.generation.flashcards import (
    FlashcardEngine, FlashcardRequestError, _math_is_from_notes, card_key, deck_schema, is_due, schedule,
)
from course_notes_rag.generation.llm import FakeLLM, LLMError, LLMUnavailableError
from course_notes_rag.models import BBox, Block, ParentChunk
from course_notes_rag.storage.db import NotFoundError
from tests.conftest import SVM

FORMULA = r"\min_{w, b} \frac{1}{2} \Vert w \Vert^2"
LEAD_IN = "The optimal margin classifier solves the following quadratic program:"
SUPPORT = "Support vectors are the training points whose functional margin is exactly one."
SLATER = "Slater's condition guarantees strong duality for convex problems."
BLOCKS = [
    Block("heading", "3 The Optimal Margin Classifier", 1, BBox(1, 50, 40, 400, 60), 2),
    Block("text", LEAD_IN, 1, BBox(1, 50, 70, 500, 90)),          # [S1]
    Block("formula", FORMULA, 1, BBox(1, 80, 100, 400, 130)),     # [S2]
    Block("text", SUPPORT, 2, BBox(2, 50, 70, 500, 90)),          # [S3]
    Block("formula", "x_i", 2, BBox(2, 50, 100, 80, 110)),        # [S4] notation, not a card
    Block("text", SLATER, 2, BBox(2, 50, 120, 500, 140)),         # [S5]
]
T0 = datetime(2026, 10, 30, 9, 0, tzinfo=timezone.utc)


def parent(blocks=BLOCKS, section="3 The Optimal Margin Classifier"):
    return ParentChunk("p1", "d1", "c1", SVM, "Lecture 4", section, ["Lecture 4", section], blocks, 120, 0)


def reply(*cards):
    return json.dumps({"cards": list(cards)})


def point(front, back, ids):
    return {"kind": "point", "front": front, "back": back, "evidence_ids": ids}


def section_cards(*responses, blocks=BLOCKS):
    llm = FakeLLM(list(responses))
    return FlashcardEngine(None, llm).cards_for_section(parent(blocks)), llm


# --- Building cards from one section ---------------------------------------------------


def test_formula_card_keeps_the_formula_verbatim_and_uses_the_models_name():
    (cards, warnings), llm = section_cards(reply(
        {"kind": "formula", "front": "State the optimal margin classifier objective.", "back": "wrong", "evidence_ids": [2]}
    ))
    assert warnings == [] and len(cards) == 1
    card = cards[0]
    assert card.kind == "formula" and card.front == "State the optimal margin classifier objective."
    assert card.back == f"$$\n{FORMULA}\n$$"  # the model's "wrong" back is ignored
    assert card.source_page == 1 and card.source_quote == f"{LEAD_IN} ${FORMULA}$"
    assert card.bboxes == [BLOCKS[1].bbox.to_dict(), BLOCKS[2].bbox.to_dict()]
    assert "[S2] $$" in llm.calls[0]["messages"][1]["content"]
    assert llm.calls[0]["json_schema"] == deck_schema()


def test_a_formula_the_model_skips_still_gets_a_card_from_its_lead_in_sentence():
    (cards, _), _ = section_cards(reply())
    assert [(c.kind, c.front) for c in cards] == [("formula", f"State the formula: {LEAD_IN}")]
    lone = [Block("heading", "Loss", 1, None, 2), Block("formula", FORMULA, 1, BBox(1, 1, 2, 3, 4))]
    (cards, _), _ = section_cards(reply(), blocks=lone)
    assert cards[0].front == "State the formula from “3 The Optimal Margin Classifier”."
    assert cards[0].source_quote == f"${FORMULA}$"


def test_a_name_that_gives_the_formula_away_is_replaced():
    (cards, _), _ = section_cards(reply(
        {"kind": "formula", "front": f"Recall $${FORMULA}$$", "back": "", "evidence_ids": [2]},
    ))
    assert cards[0].front == f"State the formula: {LEAD_IN}"
    (cards, _), _ = section_cards(reply(
        {"kind": "formula", "front": "Name the objective $\\frac{1}{2", "back": "", "evidence_ids": [2]},
    ))
    assert cards[0].front.startswith("State the formula:")  # broken LaTeX in the name


def test_key_points_are_kept_only_when_their_evidence_supports_them():
    (cards, _), _ = section_cards(reply(
        point("What are support vectors?", "The training points with functional margin exactly one.", [3]),
        point("What do kernels do?", "Kernels map inputs into a higher dimensional feature space.", [5]),
        point("What is Slater's condition?", "It guarantees strong duality.", [99]),
        point("What is the margin?", "The margin is $\\gamma", [3]),
        point("", "Support vectors have functional margin one.", [3]),
    ))
    assert [(c.kind, c.front) for c in cards] == [
        ("formula", f"State the formula: {LEAD_IN}"), ("point", "What are support vectors?"),
    ]
    kept = cards[1]
    assert kept.source_quote == SUPPORT and kept.source_page == 2 and kept.bboxes == [BLOCKS[3].bbox.to_dict()]


def test_math_the_model_retyped_must_match_its_evidence():
    formula = r"y^{(i)} (w^T x^{(i)} + b) \geq 1"
    blocks = [
        Block("text", "Every training example must keep a functional margin of at least one in the margin constraint.", 1,
              BBox(1, 50, 70, 500, 90)),
        Block("formula", formula, 1, BBox(1, 80, 100, 400, 130)),
    ]
    (cards, _), _ = section_cards(reply(
        point("What is the constraint?", r"Every example needs $y^{(i)} (w^T x^{(i)} + b) \neq 1$ as its margin constraint.", [1, 2]),
        point("What is the margin rule?", r"Every example needs $y^{(i)} (w^T x^{(i)} + b) \geq 1$ as its margin constraint.", [1, 2]),
        point("Which norm?", r"The margin constraint uses $\norm{w}$ for every training example.", [1, 2]),
    ), blocks=blocks)
    fronts = [c.front for c in cards if c.kind == "point"]
    assert fronts == ["What is the margin rule?"]  # \neq flips the fact; \norm is not in the notes


def test_math_comparison_ignores_layout_and_spelling():
    unicode_notes = "The learning rate α must satisfy 0 < α ≤ 1 in the update rule."
    assert _math_is_from_notes(r"Keep $0 < \alpha \le 1$.", unicode_notes)
    assert _math_is_from_notes(r"Use $\frac{\alpha}{1}$ here.", unicode_notes)
    assert not _math_is_from_notes(r"Keep $\alpha \geq 1$.", unicode_notes)
    assert not _math_is_from_notes(r"Use $\beta$ here.", unicode_notes)
    assert _math_is_from_notes("No math in this answer at all.", unicode_notes)


def test_math_written_in_plain_unicode_and_invented_numbers_are_caught():
    notes = "In the dual the only change is the box constraint 0 ≤ αᵢ ≤ C."
    assert _math_is_from_notes("The dual only changes the constraint to 0 ≤ αᵢ ≤ C.", notes)
    assert not _math_is_from_notes("The dual objective is ∑ᵢ αᵢ + Σᵢ ξᵢ subject to 0 ≤ αᵢ ≤ C.", notes)
    table = "| LSTM | 4m(n + m + 1) |"
    assert _math_is_from_notes("An LSTM layer has 4m(n + m + 1) parameters.", table)
    assert not _math_is_from_notes("An LSTM layer has 5m(n + m + 1) parameters.", table)


def test_formula_lines_are_listed_for_the_model_and_matched_by_content():
    (cards, _), llm = section_cards(reply(
        {"kind": "formula", "front": "State the SVM primal objective.", "back": f"$${FORMULA}$$", "evidence_ids": [1]},
    ))
    assert cards[0].front == "State the SVM primal objective."  # wrong line cited, right formula copied
    assert llm.calls[0]["messages"][1]["content"].endswith("Formulas to name: [S2]")
    text_only = [Block("text", SLATER, 1, BBox(1, 1, 2, 3, 4))]
    (_, _), llm = section_cards(reply(), blocks=text_only)
    assert llm.calls[0]["messages"][1]["content"].endswith("Formulas to name: none in this section")


def test_a_point_that_only_cites_a_formula_names_that_formula():
    (cards, _), _ = section_cards(reply(point("What is the SVM primal objective?", FORMULA, [2])))
    assert [(c.kind, c.front) for c in cards] == [("formula", "What is the SVM primal objective?")]


def test_one_malformed_card_does_not_cost_the_others():
    (cards, warnings), _ = section_cards(reply(
        {"kind": "point", "front": "Broken", "back": "no evidence"},
        {"kind": "mystery", "front": "?", "back": "?", "evidence_ids": [1]},
        point("What does Slater's condition guarantee?", "Strong duality for convex problems.", [5]),
    ))
    assert warnings == [] and [c.front for c in cards][-1] == "What does Slater's condition guarantee?"


@pytest.mark.parametrize("raw", ["not json", "[1, 2]", '{"cards": "none"}', "```json\n{oops\n```"])
def test_an_unreadable_reply_still_makes_formula_cards_with_a_warning(raw):
    (cards, warnings), _ = section_cards(raw)
    assert [c.kind for c in cards] == ["formula"]
    assert len(warnings) == 1 and "not valid JSON" in warnings[0]


def test_a_reply_wrapped_in_code_fences_is_read():
    raw = "```json\n" + reply(point("What are support vectors?", SUPPORT, [3])) + "\n```"
    (cards, warnings), _ = section_cards(raw)
    assert warnings == [] and [c.kind for c in cards] == ["formula", "point"]


def test_model_errors_skip_key_points_but_an_unavailable_model_stops():
    (cards, warnings), _ = section_cards(LLMError("timed out"))
    assert [c.kind for c in cards] == ["formula"] and "timed out" in warnings[0]
    with pytest.raises(LLMUnavailableError):
        section_cards(LLMUnavailableError("Ollama is not running"))


def test_duplicate_cards_are_merged():
    (cards, _), _ = section_cards(reply(
        point("What are support vectors?", SUPPORT, [3]),
        point("What are  support vectors?", "Points with functional margin exactly one.", [3]),
    ))
    assert [c.front for c in cards if c.kind == "point"] == ["What are support vectors?"]
    assert card_key("formula", "a", "$$ x^2 $$") == card_key("formula", "b", "$$x^2$$")
    assert card_key("point", "Same?", "a") != card_key("formula", "Same?", "a")


def test_latex_escapes_that_json_swallowed_are_restored():
    (cards, _), _ = section_cards(reply(
        point("What is the margin in terms of $\x0crac{1}{\\Vert w \\Vert}$?", SUPPORT, [3]),
    ))
    assert "\\frac{1}" in cards[1].front


# --- Scheduling ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("box", "result", "new_box", "days"),
    [(1, "good", 2, 1), (2, "good", 3, 3), (4, "good", 5, 14), (5, "good", 5, 14), (3, "again", 1, 0), (0, "good", 2, 1)],
)
def test_leitner_schedule(box, result, new_box, days):
    assert schedule(box, result, T0) == (new_box, T0 + timedelta(days=days))


def test_schedule_rejects_unknown_results_and_due_dates_compare_in_utc():
    with pytest.raises(FlashcardRequestError):
        schedule(1, "maybe", T0)
    assert is_due(None, T0)
    assert is_due((T0 - timedelta(minutes=1)).isoformat(), T0)
    assert not is_due((T0 + timedelta(days=1)).isoformat(), T0)


# --- The engine with the database -------------------------------------------------------


def test_generate_builds_a_deck_once_and_rebuilds_on_request(services, ready_course):
    cid = ready_course["course"]["id"]
    events = list(services.flashcards.generate(cid))
    names = [name for name, _ in ((e.type, e.data) for e in events)]
    assert names[0] == "deck" and names[-1] == "done" and "cards" in names and "error" not in names
    deck = services.flashcards.deck(cid)
    total = deck["stats"]["total"]
    assert total == events[-1].data["created"] > 0
    assert deck["stats"]["formulas"] > 0 and deck["stats"]["points"] > 0
    assert deck["stats"]["due"] == total and deck["stats"]["by_box"]["1"] == total

    # Every formula card's back is one of the notes' own formula blocks.
    parents = {p.parent_id: p for p in services.db.list_parents(cid)}
    for card in deck["cards"]:
        p = parents[card["parent_id"]]
        assert p.page_start <= card["source_page"] <= p.page_end
        if card["kind"] == "formula":
            assert card["back"] in [b.to_markdown() for b in p.blocks if b.type == "formula"]

    again = list(services.flashcards.generate(cid))
    assert again[0].data["sections"] == 0 and again[-1].data["created"] == 0
    rebuilt = list(services.flashcards.generate(cid, regenerate=True))
    assert rebuilt[-1].data["created"] == total
    assert services.flashcards.deck(cid)["stats"]["total"] == total


def test_reviews_move_cards_between_boxes_and_due_dates(services, ready_course):
    cid = ready_course["course"]["id"]
    list(services.flashcards.generate(cid))
    clock = {"now": T0}
    engine = FlashcardEngine(services.db, services.llm, clock=lambda: clock["now"])
    card = engine.deck(cid)["cards"][0]

    good = engine.review(card["id"], "good")
    assert good["box"] == 2 and not good["due"] and good["reviews"] == 1
    assert engine.deck(cid)["stats"]["due"] == engine.deck(cid)["stats"]["total"] - 1
    assert engine.deck(cid)["stats"]["next_due_at"] == (T0 + timedelta(days=1)).isoformat()
    clock["now"] = T0 + timedelta(days=2)
    assert next(c for c in engine.deck(cid)["cards"] if c["id"] == card["id"])["due"]

    forgot = engine.review(card["id"], "again")
    assert forgot["box"] == 1 and forgot["due"] and forgot["lapses"] == 1 and forgot["reviews"] == 2
    with pytest.raises(FlashcardRequestError):
        engine.review(card["id"], "skip")
    with pytest.raises(NotFoundError):
        engine.review("no-such-card", "good")


def test_generate_needs_processed_notes_and_a_real_course(services):
    empty = services.db.create_course("Empty")
    with pytest.raises(FlashcardRequestError, match="Upload notes"):
        next(services.flashcards.generate(empty["id"]))
    with pytest.raises(NotFoundError):
        next(services.flashcards.generate("missing"))
    with pytest.raises(NotFoundError):
        services.flashcards.deck("missing")


def test_a_section_with_problems_is_tried_again_but_a_clean_empty_one_is_not(services, ready_course):
    cid = ready_course["course"]["id"]
    sections = len(services.db.list_parents(cid))

    flaky = FlashcardEngine(services.db, FakeLLM(handler=lambda *_: "not json"))
    first = [(e.type, e.data) for e in flaky.generate(cid)]
    assert sum(n == "warning" for n, _ in first) == sections
    assert first[-1][1]["sections"] == sections
    formulas = flaky.deck(cid)["stats"]["total"]
    assert formulas == flaky.deck(cid)["stats"]["formulas"] > 0

    # The retry fills in key points, and formulas already in the deck are not duplicated.
    retry = list(services.flashcards.generate(cid))
    assert retry[0].data["sections"] == sections
    deck = services.flashcards.deck(cid)["stats"]
    assert deck["formulas"] == formulas and deck["points"] > 0

    # A clean run that found nothing still counts as done: no more model calls for it.
    quiet = FakeLLM(handler=lambda *_: reply())
    engine = FlashcardEngine(services.db, quiet)
    services.db.delete_flashcards([p.parent_id for p in services.db.list_parents(cid)])
    list(engine.generate(cid))
    calls = len(quiet.calls)
    assert calls == sections
    assert list(engine.generate(cid))[0].data["sections"] == 0 and len(quiet.calls) == calls


def test_an_unavailable_model_ends_generation_with_an_error(services, ready_course):
    cid = ready_course["course"]["id"]
    engine = FlashcardEngine(services.db, FakeLLM([LLMUnavailableError("Ollama is not running")]))
    events = [(e.type, e.data) for e in engine.generate(cid)]
    assert [n for n, _ in events] == ["deck", "progress", "error", "done"]
    assert "Ollama" in events[2][1]["message"] and events[3][1]["created"] == 0
    assert engine.deck(cid)["cards"] == []


def test_cards_go_away_with_their_document(services, ready_course):
    cid = ready_course["course"]["id"]
    list(services.flashcards.generate(cid))
    svm_id = ready_course["docs"][SVM]["id"]
    assert any(c["document_id"] == svm_id for c in services.flashcards.deck(cid)["cards"])
    services.ingestion.delete_document(svm_id)
    assert all(c["document_id"] != svm_id for c in services.flashcards.deck(cid)["cards"])


def test_add_flashcards_with_no_cards_stores_nothing(services, ready_course):
    p = services.db.list_parents(ready_course["course"]["id"])[0]
    assert services.db.add_flashcards(p, []) == []
    assert services.db.delete_flashcards([]) == 0
    with pytest.raises(NotFoundError):
        services.db.record_flashcard_review("missing", box=2, due_at=T0.isoformat(), lapse=False)
