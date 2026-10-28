"""Request bodies for the HTTP API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class CourseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    code: str | None = Field(default=None, max_length=40)

    @field_validator("name")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name must not be blank")
        return v.strip()


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=20000)


class ChatRequest(BaseModel):
    course_id: str
    question: str = Field(min_length=1, max_length=2000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=40)
    document_ids: list[str] | None = None
    expansion: Literal["none", "multi_query", "hyde", "both"] | None = None

    @field_validator("question")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("question must not be blank")
        return v.strip()


class SearchRequest(BaseModel):
    course_id: str
    query: str = Field(min_length=1, max_length=2000)
    document_ids: list[str] | None = None
    expansion: Literal["none", "multi_query", "hyde", "both"] | None = None
    top_n: int | None = Field(default=None, ge=1, le=15)


class QuizCreate(BaseModel):
    course_id: str
    topic: str | None = Field(default=None, max_length=300)
    num_questions: int = Field(default=5, ge=1, le=10)
    bloom_levels: list[Literal["Recall", "Conceptual", "Application"]] = Field(
        default_factory=lambda: ["Recall", "Conceptual", "Application"], min_length=1
    )
    document_ids: list[str] | None = None
    adaptive: bool = True
    seed: int | None = None


class AnswerSubmit(BaseModel):
    label: Literal["A", "B", "C", "D"]
