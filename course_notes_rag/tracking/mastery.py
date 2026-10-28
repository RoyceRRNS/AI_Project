"""Concept mastery scores and the adaptive sampling that targets weak sections."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, TypeVar

import numpy as np

from course_notes_rag.storage.db import Database

T = TypeVar("T")


def update_mastery(current: float, outcome: float, alpha: float = 0.25) -> float:
    """``M <- clip(M + alpha * (S - M), 0, 1)``."""
    if not 0.0 <= current <= 1.0:
        raise ValueError("mastery must be within [0, 1]")
    if outcome not in (0.0, 1.0):
        raise ValueError("outcome must be 0 or 1")
    if not 0.0 < alpha <= 1.0:
        raise ValueError("alpha must be within (0, 1]")
    return float(np.clip(current + alpha * (outcome - current), 0.0, 1.0))


def selection_weights(masteries: Sequence[float], offset: float = 1.05) -> np.ndarray:
    """``P(select c) ∝ (offset - M_c)^2``; returned weights sum to 1."""
    if len(masteries) == 0:
        return np.array([], dtype=float)
    m = np.asarray(masteries, dtype=float)
    if np.any(m < 0.0) or np.any(m > 1.0):
        raise ValueError("mastery must be within [0, 1]")
    weights = (offset - m) ** 2
    return weights / weights.sum()


def weighted_sample(
    items: Sequence[T], weights: np.ndarray, k: int, rng: np.random.Generator
) -> list[T]:
    """Sample ``k`` distinct items with the given probabilities (Efraimidis-Spirakis)."""
    if k <= 0 or not items:
        return []
    k = min(k, len(items))
    w = np.asarray(weights, dtype=float)
    keys = rng.random(len(items)) ** (1.0 / np.maximum(w, 1e-12))
    order = np.argsort(-keys)[:k]
    return [items[i] for i in order]


class MasteryTracker:
    def __init__(self, db: Database, *, initial: float = 0.5, alpha: float = 0.25) -> None:
        self.db = db
        self.initial = initial
        self.alpha = alpha

    def scores(self, parent_ids: list[str]) -> dict[str, float]:
        stored = self.db.get_mastery(parent_ids)
        return {pid: stored.get(pid, self.initial) for pid in parent_ids}

    def record(self, parent_id: str, course_id: str, correct: bool) -> tuple[float, float]:
        before = self.scores([parent_id])[parent_id]
        after = update_mastery(before, 1.0 if correct else 0.0, self.alpha)
        self.db.upsert_mastery(parent_id, course_id, after, correct)
        return before, after

    def course_summary(self, course_id: str) -> dict[str, Any]:
        sections = self.db.course_mastery(course_id, self.initial)
        attempted = [s for s in sections if s["attempts"] > 0]
        overall = (
            float(np.mean([s["score"] for s in sections])) if sections else self.initial
        )
        return {
            "course_id": course_id,
            "overall": round(overall, 4),
            "sections": sections,
            "attempted_sections": len(attempted),
            "total_sections": len(sections),
        }
