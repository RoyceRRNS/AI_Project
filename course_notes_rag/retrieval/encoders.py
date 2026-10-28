"""Embedding and reranking models, plus deterministic offline stand-ins for tests."""

from __future__ import annotations

import hashlib
import math
import threading
from collections import Counter
from typing import Protocol

import numpy as np

from course_notes_rag.retrieval.lexical import tokenize


class Embedder(Protocol):
    dim: int
    name: str

    def encode(self, texts: list[str]) -> np.ndarray: ...


class Reranker(Protocol):
    name: str

    def score(self, query: str, documents: list[str]) -> list[float]: ...


def resolve_device(device: str) -> str:
    if device != "auto":
        return device
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:  # pragma: no cover
        return "cpu"


def _half_precision(device: str) -> dict:  # pragma: no cover - live only
    if device != "cuda":
        return {}
    import torch

    return {"model_kwargs": {"dtype": torch.float16}}


class SentenceTransformerEmbedder:  # pragma: no cover - exercised by live tests
    """BGE-M3 (or any sentence-transformers model), loaded lazily on first use."""

    def __init__(self, model_name: str, device: str = "auto", batch_size: int = 16) -> None:
        self.name = model_name
        self.device = resolve_device(device)
        self.batch_size = batch_size
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        with self._lock:
            if self._model is None:
                from sentence_transformers import SentenceTransformer

                kwargs = _half_precision(self.device)
                self._model = SentenceTransformer(self.name, device=self.device, **kwargs)
        return self._model

    @property
    def dim(self) -> int:
        model = self._load()
        getter = getattr(model, "get_embedding_dimension", None) or model.get_sentence_embedding_dimension
        return int(getter())

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        model = self._load()
        return np.asarray(
            model.encode(texts, normalize_embeddings=True, batch_size=self.batch_size,
                         show_progress_bar=False),
            dtype=np.float32,
        )


class CrossEncoderReranker:  # pragma: no cover - exercised by live tests
    """bge-reranker-v2-m3 cross-encoder; scores are sigmoid probabilities in [0, 1]."""

    def __init__(self, model_name: str, device: str = "auto", max_length: int = 1024) -> None:
        self.name = model_name
        self.device = resolve_device(device)
        self.max_length = max_length
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        with self._lock:
            if self._model is None:
                from sentence_transformers import CrossEncoder

                kwargs = _half_precision(self.device)
                self._model = CrossEncoder(
                    self.name, device=self.device, max_length=self.max_length, **kwargs
                )
        return self._model

    def score(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        model = self._load()
        raw = np.asarray(
            model.predict([(query, d) for d in documents], batch_size=8, show_progress_bar=False),
            dtype=np.float64,
        )
        # Single-logit rerankers return logits unless an activation is configured.
        if raw.min() < 0.0 or raw.max() > 1.0:
            raw = 1.0 / (1.0 + np.exp(-raw))
        return [float(x) for x in raw]


class HashingEmbedder:
    """Deterministic bag-of-words embedding (feature hashing) for offline tests."""

    def __init__(self, dim: int = 384) -> None:
        if dim <= 0:
            raise ValueError("dim must be positive")
        self.dim = dim
        self.name = f"hashing-{dim}"

    def _bucket(self, token: str) -> tuple[int, float]:
        digest = hashlib.md5(token.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:4], "little") % self.dim
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        return idx, sign

    def encode(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for token, count in Counter(tokenize(text)).items():
                idx, sign = self._bucket(token)
                out[row, idx] += sign * (1.0 + math.log(count))
            norm = np.linalg.norm(out[row])
            if norm > 0:
                out[row] /= norm
        return out


class LexicalReranker:
    """Offline reranker: share of distinct query terms found in the document, in [0, 1]."""

    name = "lexical-overlap"

    def score(self, query: str, documents: list[str]) -> list[float]:
        q = set(tokenize(query))
        if not q:
            return [0.0 for _ in documents]
        scores = []
        for doc in documents:
            d = set(tokenize(doc))
            scores.append(len(q & d) / len(q))
        return scores
