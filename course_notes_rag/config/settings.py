"""Central configuration: model names, chunk sizes, DB paths and thresholds.

Every value can be overridden with an environment variable prefixed ``CNR_``
(for example ``CNR_RERANK_THRESHOLD=0.4``) or a ``.env`` file in the project root.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CNR_", env_file=PROJECT_ROOT / ".env", extra="ignore"
    )

    # "test" swaps every heavy model for a deterministic offline stand-in.
    profile: Literal["production", "test"] = "production"

    # --- Storage -----------------------------------------------------------
    storage_dir: Path = PROJECT_ROOT / "storage"

    # --- Parsing -------------------------------------------------------------
    parser_engine: Literal["auto", "docling", "pymupdf"] = "auto"
    docling_formula_enrichment: bool = True
    docling_ocr: bool = True
    max_upload_mb: int = Field(default=100, gt=0)

    # --- Chunking ------------------------------------------------------------
    parent_min_tokens: int = Field(default=200, gt=0)
    parent_target_tokens: int = Field(default=800, gt=0)
    parent_max_tokens: int = Field(default=1200, gt=0)
    child_min_tokens: int = Field(default=200, gt=0)
    child_max_tokens: int = Field(default=300, gt=0)
    child_overlap_tokens: int = Field(default=50, ge=0)

    # --- Models --------------------------------------------------------------
    embedding_model: str = "BAAI/bge-m3"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    reranker_max_length: int = 1024
    device: Literal["auto", "cuda", "cpu"] = "auto"
    collection_name: str = "course_notes"

    # --- Retrieval -----------------------------------------------------------
    dense_top_k: int = Field(default=25, gt=0)
    sparse_top_k: int = Field(default=25, gt=0)
    rrf_k: int = Field(default=60, gt=0)
    fused_top_children: int = Field(default=15, gt=0)
    final_top_n: int = Field(default=5, gt=0)
    rerank_threshold: float = Field(default=0.35, ge=0.0, le=1.0)
    query_expansion: Literal["none", "multi_query", "hyde", "both"] = "none"
    multi_query_count: int = Field(default=3, ge=1, le=6)

    # --- LLM -----------------------------------------------------------------
    llm_provider: Literal["ollama", "openai_compat", "fake"] = "ollama"
    llm_model: str = "qwen2.5:7b"
    ollama_base_url: str = "http://localhost:11434"
    openai_base_url: str | None = None
    openai_api_key: str | None = None
    llm_temperature: float = 0.2
    quiz_temperature: float = 0.4
    llm_num_ctx: int = 12288
    llm_timeout_s: float = 300.0
    chat_history_turns: int = 3

    # --- Quiz & mastery ------------------------------------------------------
    quiz_default_questions: int = 5
    quiz_max_questions: int = 10
    quiz_max_retries: int = 2
    mastery_initial: float = Field(default=0.5, ge=0.0, le=1.0)
    mastery_alpha: float = Field(default=0.25, gt=0.0, le=1.0)
    adaptive_fraction: float = Field(default=0.7, ge=0.0, le=1.0)
    adaptive_offset: float = Field(default=1.05, gt=1.0)

    # --- Server --------------------------------------------------------------
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    frontend_dist: Path = PROJECT_ROOT / "frontend" / "dist"

    @model_validator(mode="after")
    def _check_ranges(self) -> "Settings":
        if not self.parent_min_tokens <= self.parent_target_tokens <= self.parent_max_tokens:
            raise ValueError("parent token bounds must satisfy min <= target <= max")
        if self.child_min_tokens > self.child_max_tokens:
            raise ValueError("child_min_tokens must not exceed child_max_tokens")
        if self.child_overlap_tokens >= self.child_min_tokens:
            raise ValueError("child_overlap_tokens must be smaller than child_min_tokens")
        if self.quiz_default_questions > self.quiz_max_questions:
            raise ValueError("quiz_default_questions must not exceed quiz_max_questions")
        return self

    @property
    def qdrant_path(self) -> Path:
        return self.storage_dir / "qdrant_db"

    @property
    def sqlite_path(self) -> Path:
        return self.storage_dir / "metadata.sqlite"

    @property
    def uploads_dir(self) -> Path:
        return self.storage_dir / "uploads"

    def ensure_dirs(self) -> None:
        for path in (self.storage_dir, self.qdrant_path, self.uploads_dir):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
