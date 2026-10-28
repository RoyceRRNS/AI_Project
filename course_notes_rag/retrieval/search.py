"""Hybrid retrieval: (expanded) queries -> dense + BM25 -> RRF -> parent promotion -> reranking."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from course_notes_rag.generation import prompts
from course_notes_rag.generation.llm import LLMClient, LLMError
from course_notes_rag.models import BBox, RetrievedChunk
from course_notes_rag.retrieval.encoders import Embedder, Reranker
from course_notes_rag.retrieval.fusion import reciprocal_rank_fusion
from course_notes_rag.retrieval.indexer import CourseNotesIndexer
from course_notes_rag.storage.db import Database
from course_notes_rag.text_utils import linearize_table

log = logging.getLogger(__name__)

EXPANSION_MODES = ("none", "multi_query", "hyde", "both")


@dataclass
class RetrievalResult:
    query: str
    queries: list[str]
    chunks: list[RetrievedChunk]
    not_found: bool
    candidates: list[RetrievedChunk] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "queries": self.queries,
            "not_found": self.not_found,
            "chunks": [c.to_public_dict() for c in self.chunks],
            "candidates": [c.to_public_dict() for c in self.candidates],
            "timings": self.timings,
        }


class QueryExpander:
    """Multi-query rewriting and HyDE hypothetical passages via the LLM."""

    def __init__(self, llm: LLMClient, n_queries: int = 3) -> None:
        self.llm = llm
        self.n_queries = n_queries

    def multi_query(self, query: str) -> list[str]:
        raw = self.llm.chat(prompts.multi_query_messages(query, self.n_queries), temperature=0.3)
        lines = [ln.strip(" -*\t0123456789.)") for ln in raw.splitlines()]
        seen = {query.lower()}
        out = []
        for line in lines:
            if line and line.lower() not in seen:
                seen.add(line.lower())
                out.append(line)
        return out[: self.n_queries]

    def hyde(self, query: str) -> str:
        return self.llm.chat(prompts.hyde_messages(query), temperature=0.3).strip()


class HybridRetriever:
    def __init__(
        self, db: Database, indexer: CourseNotesIndexer, embedder: Embedder, reranker: Reranker,
        expander: QueryExpander | None = None, *, dense_top_k: int = 25, sparse_top_k: int = 25,
        rrf_k: int = 60, fused_top_children: int = 15, final_top_n: int = 5,
        rerank_threshold: float = 0.35, default_expansion: str = "none",
    ) -> None:
        self.db = db
        self.indexer = indexer
        self.embedder = embedder
        self.reranker = reranker
        self.expander = expander
        self.dense_top_k = dense_top_k
        self.sparse_top_k = sparse_top_k
        self.rrf_k = rrf_k
        self.fused_top_children = fused_top_children
        self.final_top_n = final_top_n
        self.rerank_threshold = rerank_threshold
        self.default_expansion = default_expansion

    def _expand(self, query: str, mode: str) -> tuple[list[str], list[str]]:
        """Returns (lexical+dense queries, dense-only texts such as HyDE passages)."""
        queries, dense_only = [query], []
        if mode == "none" or self.expander is None:
            return queries, dense_only
        try:
            if mode in ("multi_query", "both"):
                queries.extend(self.expander.multi_query(query))
            if mode in ("hyde", "both"):
                passage = self.expander.hyde(query)
                if passage:
                    dense_only.append(passage)
        except LLMError as exc:
            log.info("query expansion skipped: %s", exc)
        return queries, dense_only

    def retrieve(
        self, query: str, course_id: str, *, document_ids: list[str] | None = None,
        top_n: int | None = None, threshold: float | None = None, expansion: str | None = None,
    ) -> RetrievalResult:
        query = (query or "").strip()
        if not query:
            raise ValueError("query must not be empty")
        mode = expansion or self.default_expansion
        if mode not in EXPANSION_MODES:
            raise ValueError(f"expansion must be one of {EXPANSION_MODES}")
        top_n = top_n or self.final_top_n
        threshold = self.rerank_threshold if threshold is None else threshold
        timings: dict[str, float] = {}
        t0 = time.perf_counter()

        queries, dense_only = self._expand(query, mode)
        timings["expansion_ms"] = _ms(t0)

        t1 = time.perf_counter()
        rankings: list[list[str]] = []
        vectors = self.embedder.encode(queries + dense_only)
        for vec in vectors:
            hits = self.indexer.dense_search(vec.tolist(), course_id, self.dense_top_k, document_ids)
            rankings.append([cid for cid, _ in hits])
        timings["dense_ms"] = _ms(t1)
        t2 = time.perf_counter()
        for q in queries:
            hits = self.indexer.sparse.search(course_id, q, self.sparse_top_k, document_ids)
            rankings.append([cid for cid, _ in hits])
        timings["sparse_ms"] = _ms(t2)

        fused = reciprocal_rank_fusion(rankings, k=self.rrf_k)[: self.fused_top_children]
        if not fused:
            timings["total_ms"] = _ms(t0)
            return RetrievalResult(query, queries, [], True, [], timings)

        # Parent promotion: map the best children back to their unique parents.
        children = self.db.get_children([cid for cid, _ in fused])
        order: list[str] = []
        parent_rrf: dict[str, float] = {}
        parent_children: dict[str, list[str]] = {}
        for cid, score in fused:
            child = children.get(cid)
            if child is None:
                continue
            pid = child.parent_id
            if pid not in parent_rrf:
                order.append(pid)
                parent_rrf[pid] = score
            parent_children.setdefault(pid, []).append(cid)
        parents = self.db.get_parents(order)
        candidates: list[RetrievedChunk] = []
        for pid in order:
            parent = parents.get(pid)
            if parent is None:
                continue
            # Highlight the best-ranked child inside this parent: the passage that matched.
            highlight: list[BBox] = list(children[parent_children[pid][0]].bboxes)
            candidates.append(
                RetrievedChunk(
                    parent_id=pid,
                    document_id=parent.document_id,
                    course_id=parent.course_id,
                    pdf_name=parent.pdf_name,
                    lecture_title=parent.lecture_title,
                    section_title=parent.section_title,
                    blocks=parent.blocks,
                    page_start=parent.page_start,
                    page_end=parent.page_end,
                    rrf_score=parent_rrf[pid],
                    matched_child_ids=parent_children[pid],
                    highlight_bboxes=highlight,
                )
            )

        t3 = time.perf_counter()
        # The reranker sees section context and row-sentence tables; the LLM keeps the Markdown.
        scores = self.reranker.score(query, [_rerank_text(c) for c in candidates])
        for chunk, score in zip(candidates, scores):
            chunk.rerank_score = float(score)
        candidates.sort(key=lambda c: c.rerank_score, reverse=True)
        timings["rerank_ms"] = _ms(t3)
        kept = [c for c in candidates if c.rerank_score > threshold][:top_n]
        timings["total_ms"] = _ms(t0)
        return RetrievalResult(query, queries, kept, not kept, candidates, timings)


def _rerank_text(chunk: RetrievedChunk) -> str:
    body = "\n\n".join(
        linearize_table(b.text) if b.type == "table" else b.to_markdown() for b in chunk.blocks
    )
    return f"{chunk.lecture_title} | {chunk.section_title}\n\n{body}"


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)
