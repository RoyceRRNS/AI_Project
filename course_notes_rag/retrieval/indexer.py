"""Dense (Qdrant + BGE-M3) and sparse (bm25s) indexing of child chunks."""

from __future__ import annotations

import re
import threading

from qdrant_client import QdrantClient, models

from course_notes_rag.models import ChildChunk
from course_notes_rag.retrieval.encoders import Embedder
from course_notes_rag.retrieval.sparse import SparseIndexManager

UPSERT_BATCH = 64


def collection_for(base: str, embedder: Embedder) -> str:
    """One collection per embedding model, so switching models never mixes vector spaces."""
    slug = re.sub(r"[^a-z0-9]+", "-", embedder.name.lower()).strip("-")
    return f"{base}__{slug}"


class CourseNotesIndexer:
    def __init__(
        self, client: QdrantClient, embedder: Embedder, sparse: SparseIndexManager,
        collection_name: str = "course_notes",
    ) -> None:
        self.client = client
        self.encoder = embedder
        self.sparse = sparse
        self.collection = collection_for(collection_name, embedder)
        self._lock = threading.Lock()
        self._ready = False

    def _init_collection(self) -> None:
        with self._lock:
            if self._ready:
                return
            dim = self.encoder.dim
            if self.client.collection_exists(self.collection):
                info = self.client.get_collection(self.collection)
                size = info.config.params.vectors.size  # type: ignore[union-attr]
                if size != dim:
                    raise RuntimeError(
                        f"Collection {self.collection} has dimension {size}, model gives {dim}"
                    )
            else:
                self.client.create_collection(
                    collection_name=self.collection,
                    vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE),
                )
                for key in ("course_id", "document_id", "parent_id"):
                    self.client.create_payload_index(
                        self.collection, key, field_schema=models.PayloadSchemaType.KEYWORD
                    )
            self._ready = True

    def index_chunks(self, chunks: list[ChildChunk]) -> int:
        if not chunks:
            return 0
        self._init_collection()
        for start in range(0, len(chunks), UPSERT_BATCH):
            batch = chunks[start : start + UPSERT_BATCH]
            embeddings = self.encoder.encode([c.contextualized_text for c in batch])
            points = [
                models.PointStruct(
                    id=c.chunk_id,
                    vector=embeddings[i].tolist(),
                    payload={
                        "parent_id": c.parent_id,
                        "document_id": c.document_id,
                        "raw_text": c.raw_text,
                        "course_id": c.course_id,
                        "pdf_name": c.pdf_name,
                        "page_num": c.page_num,
                        "section_title": c.section_title,
                    },
                )
                for i, c in enumerate(batch)
            ]
            self.client.upsert(collection_name=self.collection, points=points, wait=True)
        self.sparse.invalidate(chunks[0].course_id)
        return len(chunks)

    @staticmethod
    def _filter(course_id: str, document_ids: list[str] | None = None) -> models.Filter:
        must: list[models.Condition] = [
            models.FieldCondition(key="course_id", match=models.MatchValue(value=course_id))
        ]
        if document_ids:
            must.append(
                models.FieldCondition(key="document_id", match=models.MatchAny(any=document_ids))
            )
        return models.Filter(must=must)

    def dense_search(
        self, query_vector: list[float], course_id: str, limit: int,
        document_ids: list[str] | None = None,
    ) -> list[tuple[str, float]]:
        self._init_collection()
        response = self.client.query_points(
            collection_name=self.collection,
            query=query_vector,
            query_filter=self._filter(course_id, document_ids),
            limit=limit,
            with_payload=False,
        )
        return [(str(p.id), float(p.score)) for p in response.points]

    def count(self, course_id: str, document_ids: list[str] | None = None) -> int:
        self._init_collection()
        return self.client.count(
            self.collection, count_filter=self._filter(course_id, document_ids), exact=True
        ).count

    def delete_document(self, course_id: str, document_id: str) -> None:
        self._init_collection()
        self.client.delete(
            self.collection,
            points_selector=models.FilterSelector(filter=self._filter(course_id, [document_id])),
            wait=True,
        )
        self.sparse.invalidate(course_id)

    def delete_course(self, course_id: str) -> None:
        self._init_collection()
        self.client.delete(
            self.collection,
            points_selector=models.FilterSelector(filter=self._filter(course_id)),
            wait=True,
        )
        self.sparse.invalidate(course_id)
