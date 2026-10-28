"""Builds and wires every component from :class:`Settings`."""

from __future__ import annotations

from dataclasses import dataclass

from qdrant_client import QdrantClient

from course_notes_rag.config.settings import Settings
from course_notes_rag.generation.chat_engine import GroundedChatEngine
from course_notes_rag.generation.llm import LLMClient, OllamaClient, OpenAICompatClient
from course_notes_rag.generation.offline_llm import ExtractiveLLM
from course_notes_rag.generation.quiz_engine import QuizEngine
from course_notes_rag.ingestion.chunker import HierarchicalChunker
from course_notes_rag.ingestion.parser import DocumentParser, PyMuPDFParser, build_parser
from course_notes_rag.ingestion.pipeline import IngestionService
from course_notes_rag.retrieval.encoders import (
    CrossEncoderReranker, Embedder, HashingEmbedder, LexicalReranker, Reranker,
    SentenceTransformerEmbedder,
)
from course_notes_rag.retrieval.indexer import CourseNotesIndexer
from course_notes_rag.retrieval.search import HybridRetriever, QueryExpander
from course_notes_rag.retrieval.sparse import SparseIndexManager
from course_notes_rag.storage.db import Database
from course_notes_rag.text_utils import TokenCounter, approx_token_count, hf_token_counter
from course_notes_rag.tracking.mastery import MasteryTracker


@dataclass
class Services:
    settings: Settings
    db: Database
    qdrant: QdrantClient
    embedder: Embedder
    reranker: Reranker
    llm: LLMClient
    parser: DocumentParser
    chunker: HierarchicalChunker
    sparse: SparseIndexManager
    indexer: CourseNotesIndexer
    retriever: HybridRetriever
    chat: GroundedChatEngine
    quiz: QuizEngine
    mastery: MasteryTracker
    ingestion: IngestionService

    def close(self) -> None:
        self.ingestion.shutdown()
        try:
            self.qdrant.close()
        finally:
            self.db.close()


def build_llm(settings: Settings) -> LLMClient:
    if settings.profile == "test" or settings.llm_provider == "fake":
        return ExtractiveLLM()
    if settings.llm_provider == "openai_compat":
        if not settings.openai_base_url:
            raise ValueError("CNR_OPENAI_BASE_URL is required for llm_provider=openai_compat")
        return OpenAICompatClient(
            settings.openai_base_url, settings.llm_model, settings.openai_api_key,
            temperature=settings.llm_temperature, timeout=settings.llm_timeout_s,
        )
    return OllamaClient(
        settings.ollama_base_url, settings.llm_model, temperature=settings.llm_temperature,
        num_ctx=settings.llm_num_ctx, timeout=settings.llm_timeout_s,
    )


def build_services(
    settings: Settings | None = None, *, llm: LLMClient | None = None,
    embedder: Embedder | None = None, reranker: Reranker | None = None,
    parser: DocumentParser | None = None, token_counter: TokenCounter | None = None,
    qdrant: QdrantClient | None = None,
) -> Services:
    settings = settings or Settings()
    settings.ensure_dirs()
    test = settings.profile == "test"

    db = Database(settings.sqlite_path)
    if qdrant is None:
        qdrant = QdrantClient(location=":memory:") if test else QdrantClient(path=str(settings.qdrant_path))
    if embedder is None:
        embedder = HashingEmbedder() if test else SentenceTransformerEmbedder(
            settings.embedding_model, settings.device
        )
    if reranker is None:
        reranker = LexicalReranker() if test else CrossEncoderReranker(
            settings.reranker_model, settings.device, settings.reranker_max_length
        )
    if llm is None:
        llm = build_llm(settings)
    if parser is None:
        parser = PyMuPDFParser() if test else build_parser(
            settings.parser_engine, formula_enrichment=settings.docling_formula_enrichment,
            ocr=settings.docling_ocr, device=settings.device,
        )
    if token_counter is None:
        token_counter = approx_token_count if test else hf_token_counter(settings.embedding_model)

    chunker = HierarchicalChunker(
        token_counter,
        parent_min_tokens=settings.parent_min_tokens,
        parent_target_tokens=settings.parent_target_tokens,
        parent_max_tokens=settings.parent_max_tokens,
        child_min_tokens=settings.child_min_tokens,
        child_max_tokens=settings.child_max_tokens,
        child_overlap_tokens=settings.child_overlap_tokens,
    )
    sparse = SparseIndexManager(lambda course_id: db.list_children(course_id))
    indexer = CourseNotesIndexer(qdrant, embedder, sparse, settings.collection_name)
    retriever = HybridRetriever(
        db, indexer, embedder, reranker, QueryExpander(llm, settings.multi_query_count),
        dense_top_k=settings.dense_top_k, sparse_top_k=settings.sparse_top_k,
        rrf_k=settings.rrf_k, fused_top_children=settings.fused_top_children,
        final_top_n=settings.final_top_n, rerank_threshold=settings.rerank_threshold,
        default_expansion=settings.query_expansion,
    )
    mastery = MasteryTracker(db, initial=settings.mastery_initial, alpha=settings.mastery_alpha)
    chat = GroundedChatEngine(retriever, llm, history_turns=settings.chat_history_turns)
    quiz = QuizEngine(
        db, llm, retriever, mastery, max_questions=settings.quiz_max_questions,
        max_retries=settings.quiz_max_retries, temperature=settings.quiz_temperature,
        adaptive_fraction=settings.adaptive_fraction, adaptive_offset=settings.adaptive_offset,
    )
    ingestion = IngestionService(
        db, parser, chunker, indexer, settings.uploads_dir, max_upload_mb=settings.max_upload_mb
    )
    return Services(settings, db, qdrant, embedder, reranker, llm, parser, chunker, sparse,
                    indexer, retriever, chat, quiz, mastery, ingestion)
