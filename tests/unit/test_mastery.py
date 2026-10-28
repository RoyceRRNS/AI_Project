import numpy as np
import pytest

from course_notes_rag.tracking.mastery import (
    MasteryTracker, selection_weights, update_mastery, weighted_sample,
)


def test_update_rule_exact_values():
    assert update_mastery(0.5, 1.0) == pytest.approx(0.625)
    assert update_mastery(0.5, 0.0) == pytest.approx(0.375)
    assert update_mastery(0.8, 1.0, alpha=0.5) == pytest.approx(0.9)


def test_update_rule_stays_within_bounds():
    assert update_mastery(1.0, 1.0) == 1.0
    assert update_mastery(0.0, 0.0) == 0.0
    m = 0.5
    for _ in range(100):
        m = update_mastery(m, 1.0)
    assert 0.99 < m <= 1.0


@pytest.mark.parametrize(
    ("m", "s", "alpha"), [(1.2, 1.0, 0.25), (-0.1, 1.0, 0.25), (0.5, 0.5, 0.25), (0.5, 1.0, 0.0), (0.5, 1.0, 1.5)]
)
def test_update_rule_rejects_bad_input(m, s, alpha):
    with pytest.raises(ValueError):
        update_mastery(m, s, alpha)


def test_selection_weights_formula():
    w = selection_weights([0.0, 0.5, 1.0])
    raw = np.array([1.05**2, 0.55**2, 0.05**2])
    assert np.allclose(w, raw / raw.sum())
    assert w[0] > w[1] > w[2]
    assert selection_weights([]).size == 0
    with pytest.raises(ValueError):
        selection_weights([1.5])


def test_weighted_sample_distinct_and_bounds():
    rng = np.random.default_rng(0)
    items = list("abcde")
    sample = weighted_sample(items, selection_weights([0.5] * 5), 3, rng)
    assert len(sample) == 3 and len(set(sample)) == 3
    assert sorted(weighted_sample(items, selection_weights([0.5] * 5), 10, rng)) == items
    assert weighted_sample(items, selection_weights([0.5] * 5), 0, rng) == []
    assert weighted_sample([], np.array([]), 3, rng) == []


def test_weighted_sample_favours_weak_sections():
    rng = np.random.default_rng(42)
    items = ["weak", "strong"]
    w = selection_weights([0.1, 0.95])
    picks = [weighted_sample(items, w, 1, rng)[0] for _ in range(2000)]
    share = picks.count("weak") / len(picks)
    expected = w[0]  # ~0.99
    assert share == pytest.approx(expected, abs=0.02)


class _FakeDB:
    def __init__(self):
        self.rows = {}

    def get_mastery(self, ids):
        return {i: self.rows[i] for i in ids if i in self.rows}

    def upsert_mastery(self, pid, course, score, correct):
        self.rows[pid] = score

    def course_mastery(self, course_id, initial):
        return [{"parent_id": k, "score": v, "attempts": 1} for k, v in self.rows.items()]


def test_tracker_records_and_defaults():
    tracker = MasteryTracker(_FakeDB(), initial=0.5, alpha=0.25)
    assert tracker.scores(["x"]) == {"x": 0.5}
    assert tracker.record("x", "c", True) == (0.5, pytest.approx(0.625))
    assert tracker.record("x", "c", False) == (pytest.approx(0.625), pytest.approx(0.46875))
    summary = tracker.course_summary("c")
    assert summary["attempted_sections"] == 1 and summary["overall"] == pytest.approx(0.4688, abs=1e-3)


def test_tracker_summary_empty_course():
    summary = MasteryTracker(_FakeDB()).course_summary("c")
    assert summary["total_sections"] == 0 and summary["overall"] == 0.5
