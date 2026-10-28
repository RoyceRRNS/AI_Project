"""Retrieval benchmark: 10 tricky lecture questions over the sample course.

Metrics per question: the rank of the first retrieved section from the expected PDF and
whether the expected page is inside it. Aggregates: Hit@1, Hit@5, MRR and page accuracy.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from course_notes_rag.retrieval.search import HybridRetriever

BENCHMARK = [
    ("Why does the vanishing gradient problem happen in RNNs and how do LSTMs fix it?", "Lecture_07_RNNs.pdf", 1),
    ("What are the KKT conditions and what does complementary slackness imply?", "Lecture_04_SVMs.pdf", 2),
    ("What does Lemma 3.2 state?", "Lecture_04_SVMs.pdf", 2),
    ("State Bayes' theorem in terms of the prior and the likelihood.", "Lecture_02_Probability_Slides.pdf", 4),
    ("Does zero covariance imply that two random variables are independent?", "Lecture_02_Probability_Slides.pdf", 5),
    ("How is the geometric margin related to the functional margin?", "Lecture_04_SVMs.pdf", 1),
    ("Which kernel corresponds to an infinite-dimensional feature space?", "Lecture_04_SVMs.pdf", 3),
    ("How does gradient clipping deal with exploding gradients?", "Lecture_07_RNNs.pdf", 1),
    ("What is the variance of a Bernoulli random variable?", "Lecture_02_Probability_Slides.pdf", 7),
    ("What happens if the learning rate alpha is too large in gradient descent?", "Handwritten_Gradient_Descent.pdf", 1),
]


@dataclass
class QuestionResult:
    question: str
    expected_pdf: str
    expected_page: int
    rank: int | None
    page_hit: bool
    top_pdf: str | None
    top_section: str | None
    top_score: float | None
    not_found: bool
    latency_ms: float


def run_benchmark(retriever: HybridRetriever, course_id: str, *, expansion: str | None = None) -> dict:
    results: list[QuestionResult] = []
    for question, pdf, page in BENCHMARK:
        t0 = time.perf_counter()
        res = retriever.retrieve(question, course_id, expansion=expansion)
        latency = (time.perf_counter() - t0) * 1000
        rank = next((i for i, c in enumerate(res.chunks, start=1) if c.pdf_name == pdf), None)
        hit = res.chunks[rank - 1] if rank else None
        results.append(
            QuestionResult(
                question=question,
                expected_pdf=pdf,
                expected_page=page,
                rank=rank,
                page_hit=bool(hit and hit.page_start <= page <= hit.page_end),
                top_pdf=res.chunks[0].pdf_name if res.chunks else None,
                top_section=res.chunks[0].section_title if res.chunks else None,
                top_score=round(res.chunks[0].rerank_score, 4) if res.chunks else None,
                not_found=res.not_found,
                latency_ms=round(latency, 1),
            )
        )
    n = len(results)
    summary = {
        "hit_at_1": sum(r.rank == 1 for r in results) / n,
        "hit_at_5": sum(r.rank is not None for r in results) / n,
        "mrr": sum(1 / r.rank for r in results if r.rank) / n,
        "page_accuracy": sum(r.page_hit for r in results) / n,
        "mean_latency_ms": round(sum(r.latency_ms for r in results) / n, 1),
    }
    return {"summary": summary, "questions": [asdict(r) for r in results]}


def write_report(report: dict, out_dir: Path, label: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"retrieval_{label}_{stamp}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path


def format_report(report: dict) -> str:
    lines = [f"{'#':>2}  {'rank':>4}  {'page':>4}  {'score':>5}  question"]
    for i, q in enumerate(report["questions"], start=1):
        rank = q["rank"] if q["rank"] else "-"
        score = f"{q['top_score']:.2f}" if q["top_score"] is not None else "  -  "
        lines.append(f"{i:>2}  {rank:>4}  {'yes' if q['page_hit'] else 'no':>4}  {score:>5}  {q['question']}")
    s = report["summary"]
    lines.append(
        f"Hit@1 {s['hit_at_1']:.0%}  Hit@5 {s['hit_at_5']:.0%}  MRR {s['mrr']:.2f}  "
        f"page accuracy {s['page_accuracy']:.0%}  mean latency {s['mean_latency_ms']} ms"
    )
    return "\n".join(lines)
