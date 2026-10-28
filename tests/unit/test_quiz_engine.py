import json
from collections import Counter

import numpy as np
import pytest
from pydantic import ValidationError

from course_notes_rag.generation.llm import FakeLLM, LLMError, LLMUnavailableError
from course_notes_rag.generation.evidence import evidence_units, format_evidence, resolve_evidence
from course_notes_rag.generation.quiz_engine import (
    QuizEngine, QuizQuestion, QuizQuestionDraft, QuizRequest, QuizRequestError, check_draft,
    draft_schema, repair_draft, restore_latex_escapes,
)
from course_notes_rag.models import BBox, Block
from course_notes_rag.storage.db import NotFoundError
from course_notes_rag.tracking.mastery import MasteryTracker

QUOTE = "The forget gate decides what information to erase from the cell state."
BLOCKS = [
    Block("heading", "4 Long Short-Term Memory", 2, BBox(2, 50, 40, 400, 60), 2),
    Block("text", "LSTMs add a memory cell. " + QUOTE, 2, BBox(2, 50, 70, 500, 120)),
    Block("text", "When the forget gate stays close to 1, the gradient flows across many steps.", 3,
          BBox(3, 50, 70, 500, 120)),
]


def draft(**over):
    data = {
        "bloom_level": "Recall",
        "question_stem": "What does the forget gate $f_t$ do?",
        "options": [
            {"text": "Erases information from the cell state", "is_correct": True,
             "trap_type": "correct", "misconception_explanation": "Matches the notes."},
            {"text": "Adds information to the cell state", "is_correct": False,
             "trap_type": "sign_inversion", "misconception_explanation": "Inverts erase/add."},
            {"text": "Erases information even when gates are disabled", "is_correct": False,
             "trap_type": "boundary_violation", "misconception_explanation": "Ignores conditions."},
            {"text": "Decides which values are exposed as $h_t$", "is_correct": False,
             "trap_type": "terminology_swap", "misconception_explanation": "That is the output gate."},
        ],
        "hint": "Think about what 'forget' means for memory.",
        "evidence_ids": [2],
    }
    data.update(over)
    return data


def as_draft(**over):
    return QuizQuestionDraft.model_validate(draft(**over))


# --- validation helpers -------------------------------------------------------------


def test_good_draft_passes():
    problems, evidence = check_draft(as_draft(), "Recall", BLOCKS)
    assert problems == [] and evidence.page == 2 and evidence.quote == QUOTE
    assert evidence.bboxes == [BLOCKS[1].bbox]


@pytest.mark.parametrize(
    ("change", "fragment"),
    [
        (lambda d: d["options"][1].update(is_correct=True), "exactly one option"),
        (lambda d: d["options"][0].update(is_correct=False, trap_type="sign_inversion"), "exactly one option"),
        (lambda d: d["options"][0].update(trap_type="terminology_swap"), 'trap_type "correct"'),
        (lambda d: d["options"][1].update(trap_type="correct"), "is a distractor"),
        (lambda d: d["options"][2].update(text=d["options"][1]["text"]), "different"),
        (lambda d: [o.update(trap_type="sign_inversion") for o in d["options"][1:]], "two different"),
        (lambda d: d.update(question_stem="Bad math $\\frac{1}{2$"), "invalid LaTeX"),
        (lambda d: d.update(evidence_ids=[99]), "do not exist"),
        (lambda d: d.update(evidence_ids=[3]), "do not support the correct option"),
        (lambda d: d.update(evidence_ids=[0, 2]), "do not exist"),
        (lambda d: d.update(bloom_level="Application"), "bloom_level"),
        (lambda d: d.update(hint="  "), "hint is empty"),
        (lambda d: d["options"][3].update(misconception_explanation=""), "misconception_explanation is empty"),
    ],
)
def test_bad_drafts_are_rejected(change, fragment):
    data = draft()
    change(data)
    problems, _ = check_draft(QuizQuestionDraft.model_validate(data), "Recall", BLOCKS)
    assert any(fragment in p for p in problems), problems


def test_repair_fixes_labelling_slips():
    data = draft()
    data["options"][0]["trap_type"] = "terminology_swap"       # correct option mislabelled
    data["options"][2]["trap_type"] = "correct"                # distractor mislabelled
    data["options"][3]["misconception_explanation"] = " "      # empty rationale
    data["evidence_ids"] = [3]                                 # evidence about something else
    fixed, repairs = repair_draft(QuizQuestionDraft.model_validate(data), BLOCKS)
    assert sorted(repairs) == sorted(["correct option relabelled", "distractor relabelled",
                                      "rationale filled", "evidence re-selected"])
    assert fixed.options[0].trap_type == "correct"
    assert fixed.options[2].trap_type in {"boundary_violation", "sign_inversion", "terminology_swap"}
    assert fixed.options[3].misconception_explanation
    assert fixed.evidence_ids == [2]
    problems, evidence = check_draft(fixed, "Recall", BLOCKS)
    assert problems == [] and evidence.quote == QUOTE


def test_latex_swallowed_by_json_escapes_is_restored():
    raw = json.dumps(draft(question_stem="Compute $FRAC{1}{2}$ and $TEXT{x}$"))
    broken = raw.replace("FRAC", "\\frac").replace("TEXT", "\\text")  # one backslash inside the JSON text
    parsed = QuizQuestionDraft.model_validate_json(broken)
    assert "\x0crac" in parsed.question_stem and "\text" in parsed.question_stem
    problems, _ = check_draft(parsed, "Recall", BLOCKS)
    assert any("Control character" in p for p in problems)
    fixed, repairs = repair_draft(parsed, BLOCKS)
    assert fixed.question_stem == "Compute $\\frac{1}{2}$ and $\\text{x}$"
    assert "LaTeX escapes restored" in repairs
    assert check_draft(fixed, "Recall", BLOCKS)[0] == []


def test_restore_latex_escapes_keeps_real_newlines():
    assert restore_latex_escapes("line one\nline two") == "line one\nline two"
    assert restore_latex_escapes("$\nabla f$ and \theta") == "$\\nabla f$ and \\theta"


def test_repair_leaves_content_problems_to_validation():
    data = draft()
    data["options"][1]["is_correct"] = True  # two correct answers: a content error
    fixed, repairs = repair_draft(QuizQuestionDraft.model_validate(data), BLOCKS)
    assert repairs == []
    unsupported = draft()
    unsupported["options"][0]["text"] = "Photosynthesis happens in chloroplasts"
    fixed, repairs = repair_draft(QuizQuestionDraft.model_validate(unsupported), BLOCKS)
    assert "evidence re-selected" not in repairs
    assert any("do not support" in p for p in check_draft(fixed, "Recall", BLOCKS)[0])


def test_retries_raise_the_temperature(services, ready_course):
    parent = services.db.list_parents(ready_course["course"]["id"])[0]
    temps = []

    class Recorder(FakeLLM):
        def chat(self, messages, *, temperature=None, json_schema=None):
            temps.append(temperature)
            return "{}"

    make_engine(services, Recorder(), max_retries=2)._generate_one(1, "Recall", "t", parent, [], np.random.default_rng(0))
    assert temps == [0.4, 0.65, 0.9]


def test_schema_requires_four_options_and_evidence():
    with pytest.raises(ValidationError):
        QuizQuestionDraft.model_validate(draft(options=draft()["options"][:3]))
    with pytest.raises(ValidationError):
        QuizQuestionDraft.model_validate(draft(evidence_ids=[]))
    with pytest.raises(ValidationError):
        QuizQuestionDraft.model_validate(draft(evidence_ids=[1, 2, 3, 4]))


def test_evidence_units_and_numbered_notes():
    units = evidence_units(BLOCKS)
    assert [(u.id, u.page) for u in units] == [(1, 2), (2, 2), (3, 3)]
    assert units[1].text == QUOTE
    notes = format_evidence(BLOCKS)
    assert notes.splitlines() == [
        "[p. 2]", "## 4 Long Short-Term Memory", "[S1] LSTMs add a memory cell.", f"[S2] {QUOTE}",
        "[p. 3]", "[S3] When the forget gate stays close to 1, the gradient flows across many steps.",
    ]


def test_resolve_evidence():
    ev, problem = resolve_evidence([3, 2, 2], BLOCKS)
    assert problem is None and ev.ids == [2, 3] and ev.page == 2
    assert ev.quote.startswith(QUOTE) and ev.quote.endswith("many steps.")
    assert len(ev.bboxes) == 2
    assert resolve_evidence([], BLOCKS)[1]
    assert "do not exist" in resolve_evidence([7], BLOCKS)[1]
    short = [Block("text", "Too short.", 1, BBox(1, 0, 0, 1, 1))]
    assert "at least" in resolve_evidence([1], short)[1]


def test_formula_blocks_are_single_evidence_units():
    blocks = [Block("formula", r"c_t = f_t \odot c_{t-1} + i_t \odot \tilde{c}_t", 4, BBox(4, 0, 0, 1, 1))]
    ev, problem = resolve_evidence([1], blocks)
    assert problem is None and ev.page == 4
    assert ev.quote == r"$c_t = f_t \odot c_{t-1} + i_t \odot \tilde{c}_t$"  # inline, renders in a sentence


def test_draft_schema_pins_bloom_level():
    schema = draft_schema("Conceptual")
    assert schema["properties"]["bloom_level"]["enum"] == ["Conceptual"]
    assert schema["properties"]["options"]["minItems"] == 4


def test_question_model_enforces_one_correct_and_labels():
    base = {
        "question_id": 1, "bloom_level": "Recall", "question_stem": "Q", "hint": "h",
        "source_pdf": "a.pdf", "source_page": 1, "source_quote": "q", "parent_id": "p",
        "document_id": "d", "section_title": "s",
    }
    opts = [{"label": lbl, "text": lbl, "is_correct": lbl == "B", "trap_type": "correct" if lbl == "B"
             else "sign_inversion", "misconception_explanation": "x"} for lbl in "ABCD"]
    q = QuizQuestion.model_validate({**base, "options": opts})
    assert q.correct_label == "B"
    public = q.public()
    assert "is_correct" not in json.dumps(public) and "misconception" not in json.dumps(public)
    assert "source_quote" not in public
    with pytest.raises(ValidationError):
        QuizQuestion.model_validate({**base, "options": [dict(o, is_correct=True) for o in opts]})
    with pytest.raises(ValidationError):
        QuizQuestion.model_validate({**base, "options": list(reversed(opts))})


# --- engine with real DB (test profile) -------------------------------------------------


def make_engine(services, llm, **kw):
    return QuizEngine(services.db, llm, services.retriever,
                      MasteryTracker(services.db), **kw)


def offline_quiz(services, course_id, **kw):
    req = QuizRequest(course_id=course_id, **kw)
    return list(services.quiz.generate(req))


def test_generate_quiz_end_to_end_with_offline_llm(services, ready_course):
    events = offline_quiz(services, ready_course["course"]["id"], topic="forget gate LSTM",
                          num_questions=3, seed=1)
    types = [e.type for e in events]
    assert types[0] == "quiz" and types[-1] == "done"
    questions = [e.data["question"] for e in events if e.type == "question"]
    assert len(questions) == 3
    for q in questions:
        assert [o["label"] for o in q["options"]] == ["A", "B", "C", "D"]
        assert q["hint"] and q["source_pdf"] and q["source_page"] >= 1
        assert "is_correct" not in json.dumps(q)
    assert {q["bloom_level"] for q in questions} == {"Recall", "Conceptual", "Application"}
    stored = services.db.get_quiz_questions(events[0].data["quiz_id"])
    assert len(stored) == 3


def test_retry_with_feedback_then_success(services, ready_course):
    parent = services.db.list_parents(ready_course["course"]["id"])[0]
    units = evidence_units(parent.blocks)
    unit = next(u for u in units if len(u.text.split()) > 8 and "$" not in u.text)
    sid = unit.id
    good = draft(evidence_ids=[sid])
    good["options"][0]["text"] = unit.text  # the correct option is stated by its evidence
    llm = FakeLLM(["not json", json.dumps(draft(evidence_ids=[999])), json.dumps(good)])
    engine = make_engine(services, llm)
    q = engine._generate_one(1, "Recall", "t", parent, [], np.random.default_rng(0))
    assert q is not None and q.source_page == next(u.page for u in units if u.id == sid)
    assert q.source_quote == next(u.text for u in units if u.id == sid)
    assert len(llm.calls) == 3
    feedback = llm.calls[2]["messages"][-1]["content"]
    assert "do not exist" in feedback
    assert "[S1]" in llm.calls[0]["messages"][-1]["content"]


def test_gives_up_after_max_retries(services, ready_course):
    parent = services.db.list_parents(ready_course["course"]["id"])[0]
    llm = FakeLLM(["{}", "{}", "{}"])
    engine = make_engine(services, llm, max_retries=2)
    assert engine._generate_one(1, "Recall", "t", parent, [], np.random.default_rng(0)) is None
    assert len(llm.calls) == 3


def test_all_failures_produce_warnings_and_error(services, ready_course):
    engine = make_engine(services, FakeLLM(handler=lambda m, s: "{}"), max_retries=0)
    events = list(engine.generate(QuizRequest(ready_course["course"]["id"], num_questions=2, seed=3)))
    types = [e.type for e in events]
    assert types.count("warning") == 2 and "error" in types and types[-1] == "done"
    assert events[-1].data["generated"] == 0


def test_model_unavailable_stops_generation(services, ready_course):
    engine = make_engine(services, FakeLLM([LLMUnavailableError("Ollama is down")]))
    events = list(engine.generate(QuizRequest(ready_course["course"]["id"], num_questions=3)))
    assert [e.type for e in events][-2:] == ["error", "done"]
    assert "Ollama is down" in events[-2].data["message"]


def test_transient_llm_error_falls_back_to_spare_section(services, ready_course):
    calls = {"n": 0}
    offline = services.llm

    def handler(messages, schema):
        calls["n"] += 1
        if calls["n"] == 1:
            raise LLMError("flaky")
        return offline.chat(messages, json_schema=schema)

    engine = make_engine(services, FakeLLM(handler=handler))
    events = list(engine.generate(QuizRequest(ready_course["course"]["id"], num_questions=1, seed=5)))
    assert [e.type for e in events].count("question") == 1


@pytest.mark.parametrize(
    "kw",
    [{"num_questions": 0}, {"num_questions": 11}, {"bloom_levels": []}, {"bloom_levels": ["Create"]},
     {"topic": "x" * 301}],
)
def test_invalid_requests(services, ready_course, kw):
    with pytest.raises(QuizRequestError):
        offline_quiz(services, ready_course["course"]["id"], **kw)


def test_unknown_course_and_empty_course(services):
    with pytest.raises(NotFoundError):
        offline_quiz(services, "missing")
    empty = services.db.create_course("Empty")
    with pytest.raises(QuizRequestError, match="Upload notes"):
        offline_quiz(services, empty["id"])


def test_topic_not_in_notes(services, ready_course):
    with pytest.raises(QuizRequestError, match="not found"):
        offline_quiz(services, ready_course["course"]["id"], topic="medieval castle architecture")


def test_options_are_shuffled(services, ready_course):
    labels = Counter()
    for seed in range(6):
        events = offline_quiz(services, ready_course["course"]["id"], num_questions=2, seed=seed)
        for e in events:
            if e.type == "question":
                row = services.db.get_quiz_question(e.data["question"]["question_id"] and
                                                    events[0].data["quiz_id"], e.data["question"]["question_id"])
                labels[QuizQuestion.model_validate(row["payload"]).correct_label] += 1
    assert len(labels) >= 2


# --- context selection -------------------------------------------------------------------


def test_adaptive_selection_targets_weak_sections(services, ready_course):
    cid = ready_course["course"]["id"]
    parents = services.db.list_parents(cid)
    assert len(parents) >= 3
    weak, strong = parents[0], parents[1:]
    for p in strong:
        services.db.upsert_mastery(p.parent_id, cid, 0.98, True)
    services.db.upsert_mastery(weak.parent_id, cid, 0.05, False)
    engine = services.quiz
    picks = Counter()
    for seed in range(200):
        slots = engine.select_contexts(parents, QuizRequest(cid, num_questions=1), False,
                                       np.random.default_rng(seed))
        picks[slots[0].parent.parent_id] += 1
    # 70% of draws are adaptive (n=1 rounds to 1 adaptive slot), so the weak one dominates.
    assert picks[weak.parent_id] / 200 > 0.8


def test_selection_mix_and_reuse(services, ready_course):
    cid = ready_course["course"]["id"]
    parents = services.db.list_parents(cid)
    rng = np.random.default_rng(0)
    slots = services.quiz.select_contexts(parents, QuizRequest(cid, num_questions=10), False, rng)
    assert len(slots) == 10
    assert sum(s.adaptive for s in slots) >= 7
    assert [s.level for s in slots[:3]] == ["Recall", "Conceptual", "Application"]
    plain = services.quiz.select_contexts(parents, QuizRequest(cid, num_questions=3, adaptive=False),
                                          True, rng)
    assert not any(s.adaptive for s in plain)
    assert [s.parent.parent_id for s in plain] and len({s.parent.parent_id for s in plain}) == 3


# --- grading -----------------------------------------------------------------------------


def _quiz_with_question(services, ready_course):
    events = offline_quiz(services, ready_course["course"]["id"], topic="forget gate", num_questions=1, seed=2)
    quiz_id = events[0].data["quiz_id"]
    qid = next(e.data["question"]["question_id"] for e in events if e.type == "question")
    row = services.db.get_quiz_question(quiz_id, qid)
    return quiz_id, qid, QuizQuestion.model_validate(row["payload"])


def test_correct_first_try_raises_mastery(services, ready_course):
    quiz_id, qid, q = _quiz_with_question(services, ready_course)
    result = services.quiz.answer(quiz_id, qid, q.correct_label.lower())
    assert result["is_correct"] and result["first_try"] and result["mastery_updated"]
    assert result["mastery_before"] == 0.5 and result["mastery_after"] == pytest.approx(0.625)
    assert result["source_quote"] and len(result["options"]) == 4
    assert all("misconception_explanation" in o for o in result["options"])


def test_wrong_first_try_lowers_mastery_and_retry_does_not_count(services, ready_course):
    quiz_id, qid, q = _quiz_with_question(services, ready_course)
    wrong = next(lbl for lbl in "ABCD" if lbl != q.correct_label)
    first = services.quiz.answer(quiz_id, qid, wrong)
    assert not first["is_correct"] and first["mastery_after"] == pytest.approx(0.375)
    retry = services.quiz.answer(quiz_id, qid, q.correct_label)
    assert retry["is_correct"] and retry["attempt_number"] == 2
    assert retry["mastery_updated"] is False and retry["mastery_after"] is None
    assert services.mastery.scores([q.parent_id])[q.parent_id] == pytest.approx(0.375)
    summary = services.quiz.get_quiz(quiz_id)
    assert summary["score"] == {"answered": 1, "correct_first_try": 0, "total": 1,
                                "by_level": {q.bloom_level: {"answered": 1, "correct": 0}}}


def test_answer_validation(services, ready_course):
    quiz_id, qid, _ = _quiz_with_question(services, ready_course)
    with pytest.raises(QuizRequestError):
        services.quiz.answer(quiz_id, qid, "E")
    with pytest.raises(NotFoundError):
        services.quiz.answer(quiz_id, 99, "A")
    with pytest.raises(NotFoundError):
        services.quiz.answer("missing", qid, "A")


def test_answer_after_section_deleted(services, ready_course):
    quiz_id, qid, q = _quiz_with_question(services, ready_course)
    services.ingestion.delete_document(q.document_id)
    result = services.quiz.answer(quiz_id, qid, q.correct_label)
    assert result["is_correct"] and result["mastery_updated"] is False
