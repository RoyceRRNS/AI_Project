"""Streaming grounded RAG answers with verified inline citations."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from course_notes_rag.generation import prompts
from course_notes_rag.generation.citations import verify_citations
from course_notes_rag.generation.llm import LLMClient, LLMError

log = logging.getLogger(__name__)

MAX_QUESTION_CHARS = 2000


@dataclass
class ChatEvent:
    type: str
    data: dict[str, Any]


class ChatRequestError(ValueError):
    pass


class GroundedChatEngine:
    def __init__(self, retriever: Any, llm: LLMClient, *, history_turns: int = 3) -> None:
        self.retriever = retriever
        self.llm = llm
        self.history_turns = history_turns

    def _clean_history(self, history: list[dict[str, str]] | None) -> list[dict[str, str]]:
        cleaned = []
        for turn in history or []:
            role, content = turn.get("role"), (turn.get("content") or "").strip()
            if role in ("user", "assistant") and content:
                cleaned.append({"role": role, "content": content[:4000]})
        return cleaned[-2 * self.history_turns :]

    def standalone_question(self, question: str, history: list[dict[str, str]]) -> str:
        if not history:
            return question
        try:
            rewritten = self.llm.chat(prompts.condense_messages(question, history), temperature=0.0)
        except LLMError as exc:
            log.info("condense failed, using raw question: %s", exc)
            return question
        rewritten = rewritten.strip().strip('"').strip()
        return rewritten if 0 < len(rewritten) <= MAX_QUESTION_CHARS else question

    def stream_answer(
        self, course_id: str, course_name: str, question: str, *,
        history: list[dict[str, str]] | None = None, document_ids: list[str] | None = None,
        expansion: str | None = None,
    ) -> Iterator[ChatEvent]:
        question = (question or "").strip()
        if not question:
            raise ChatRequestError("Question must not be empty")
        if len(question) > MAX_QUESTION_CHARS:
            raise ChatRequestError(f"Question is too long (max {MAX_QUESTION_CHARS} characters)")
        turns = self._clean_history(history)

        yield ChatEvent("status", {"stage": "retrieving"})
        try:
            query = self.standalone_question(question, turns)
            result = self.retriever.retrieve(
                query, course_id, document_ids=document_ids, expansion=expansion
            )
        except LLMError as exc:
            yield ChatEvent("error", {"message": str(exc)})
            return
        yield ChatEvent(
            "sources",
            {
                "query": query,
                "expanded_queries": result.queries[1:],
                "sources": [c.to_public_dict() for c in result.chunks],
                "timings": result.timings,
            },
        )
        if result.not_found:
            yield ChatEvent("not_found", {"message": prompts.NOT_FOUND_MESSAGE})
            yield ChatEvent("done", {"answer": prompts.NOT_FOUND_MESSAGE, "not_found": True})
            return

        yield ChatEvent("status", {"stage": "generating"})
        messages = prompts.chat_messages(course_name, question, result.chunks, turns)
        parts: list[str] = []
        try:
            for delta in self.llm.stream_chat(messages):
                parts.append(delta)
                yield ChatEvent("token", {"text": delta})
        except LLMError as exc:
            yield ChatEvent("error", {"message": str(exc)})
            return
        answer = "".join(parts).strip()
        not_found = prompts.NOT_FOUND_MESSAGE.split(":")[0].lower() in answer.lower()[:80]
        report = verify_citations(answer, result.chunks, complete=not not_found)
        yield ChatEvent("citations", report.to_dict())
        yield ChatEvent("done", {"answer": report.annotated_answer or answer, "not_found": not_found})
