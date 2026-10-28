"""Reciprocal Rank Fusion."""

from __future__ import annotations

from collections.abc import Sequence


def reciprocal_rank_fusion(rankings: Sequence[Sequence[str]], k: int = 60) -> list[tuple[str, float]]:
    """Fuse ranked id lists: ``score(d) = sum_m 1 / (k + rank_m(d))`` with 1-based ranks.

    Ties keep the order in which ids were first seen, so results are deterministic.
    """
    if k <= 0:
        raise ValueError("k must be positive")
    scores: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    seen = 0
    for ranking in rankings:
        dupes: set[str] = set()
        for rank, doc_id in enumerate(ranking, start=1):
            if doc_id in dupes:
                continue
            dupes.add(doc_id)
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
            if doc_id not in first_seen:
                first_seen[doc_id] = seen
                seen += 1
    return sorted(scores.items(), key=lambda item: (-item[1], first_seen[item[0]]))
