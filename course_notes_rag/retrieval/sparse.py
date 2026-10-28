"""BM25 (bm25s) keyword index per course, rebuilt from SQLite whenever the course changes."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from course_notes_rag.models import ChildChunk
from course_notes_rag.retrieval.lexical import tokenize


@dataclass
class _CourseIndex:
    retriever: object | None
    chunk_ids: list[str]
    document_ids: list[str]
    vocab: set[str]


class SparseIndex:
    """BM25 over child chunks of one course."""

    def __init__(self, children: list[ChildChunk]) -> None:
        import bm25s

        self.chunk_ids = [c.chunk_id for c in children]
        self.document_ids = [c.document_id for c in children]
        corpus = [tokenize(f"{c.section_title}\n{c.raw_text}") for c in children]
        self.vocab = {t for doc in corpus for t in doc}
        self._retriever = None
        if children and self.vocab:
            # bm25s cannot index empty documents; give them a token no query can produce.
            corpus = [doc or ["\x00empty"] for doc in corpus]
            self._retriever = bm25s.BM25()
            self._retriever.index(corpus, show_progress=False)

    def __len__(self) -> int:
        return len(self.chunk_ids)

    def search(
        self, query: str, k: int, document_ids: list[str] | None = None
    ) -> list[tuple[str, float]]:
        if self._retriever is None or k <= 0:
            return []
        q_tokens = [t for t in tokenize(query) if t in self.vocab]
        if not q_tokens:
            return []
        n = len(self.chunk_ids)
        # Over-fetch when filtering by document so the filter cannot starve the result list.
        fetch = n if document_ids else min(k, n)
        idx, scores = self._retriever.retrieve([q_tokens], k=fetch, show_progress=False)
        allowed = set(document_ids) if document_ids else None
        out: list[tuple[str, float]] = []
        for i, score in zip(np.asarray(idx)[0], np.asarray(scores)[0]):
            if score <= 0:
                continue
            i = int(i)
            if allowed is not None and self.document_ids[i] not in allowed:
                continue
            out.append((self.chunk_ids[i], float(score)))
            if len(out) >= k:
                break
        return out


class SparseIndexManager:
    """Caches one :class:`SparseIndex` per course; callers invalidate on ingest/delete."""

    def __init__(self, load_children: Callable[[str], list[ChildChunk]]) -> None:
        self._load = load_children
        self._cache: dict[str, SparseIndex] = {}
        self._lock = threading.Lock()

    def get(self, course_id: str) -> SparseIndex:
        with self._lock:
            index = self._cache.get(course_id)
            if index is None:
                index = SparseIndex(self._load(course_id))
                self._cache[course_id] = index
            return index

    def invalidate(self, course_id: str) -> None:
        with self._lock:
            self._cache.pop(course_id, None)

    def search(
        self, course_id: str, query: str, k: int, document_ids: list[str] | None = None
    ) -> list[tuple[str, float]]:
        return self.get(course_id).search(query, k, document_ids)
