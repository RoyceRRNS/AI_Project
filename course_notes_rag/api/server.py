"""FastAPI backend: library (/courses, /documents), grounded chat (/chat), quizzes (/quiz)."""

from __future__ import annotations

import itertools
import json
import logging
from collections.abc import Iterable, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from course_notes_rag import __version__
from course_notes_rag.api.schemas import (
    AnswerSubmit, ChatRequest, CourseCreate, QuizCreate, SearchRequest,
)
from course_notes_rag.config.settings import Settings
from course_notes_rag.generation.chat_engine import ChatRequestError
from course_notes_rag.generation.quiz_engine import QuizRequest, QuizRequestError
from course_notes_rag.ingestion.pipeline import UploadError
from course_notes_rag.services import Services, build_services
from course_notes_rag.storage.db import NotFoundError

log = logging.getLogger(__name__)

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"}


def _sse(events: Iterable[Any]) -> Iterator[str]:
    try:
        for event in events:
            yield f"event: {event.type}\ndata: {json.dumps(event.data, ensure_ascii=False)}\n\n"
    except Exception as exc:  # noqa: BLE001 - surface failures to the client as an event
        log.exception("stream failed")
        payload = json.dumps({"message": f"Unexpected error: {exc}"})
        yield f"event: error\ndata: {payload}\n\n"
        yield "event: done\ndata: {}\n\n"


def create_app(services: Services | None = None, settings: Settings | None = None) -> FastAPI:
    state: dict[str, Services] = {}

    def svc() -> Services:
        if "s" not in state:
            state["s"] = services or build_services(settings)
        return state["s"]

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        svc()
        yield
        if services is None and "s" in state:
            state["s"].close()

    app = FastAPI(title="Course Notes RAG", version=__version__, lifespan=lifespan)
    cfg = (services.settings if services else settings) or Settings()
    app.add_middleware(
        CORSMiddleware, allow_origins=cfg.cors_origins, allow_methods=["*"], allow_headers=["*"]
    )

    @app.exception_handler(NotFoundError)
    async def _not_found(_request: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(UploadError)
    async def _upload(_request: Request, exc: UploadError) -> JSONResponse:
        return JSONResponse({"detail": str(exc), "existing": exc.existing}, status_code=exc.status_code)

    @app.exception_handler(QuizRequestError)
    async def _quiz_err(_request: Request, exc: QuizRequestError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(ChatRequestError)
    async def _chat_err(_request: Request, exc: ChatRequestError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    # --- Health --------------------------------------------------------------

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        s = svc()
        llm = s.llm.health()
        return {
            "status": "ok",
            "version": __version__,
            "profile": s.settings.profile,
            "llm": llm,
            "models": {
                "embedding": s.embedder.name,
                "reranker": s.reranker.name,
                "llm": s.llm.model,
                "parser": s.parser.name,
            },
            "retrieval": {
                "dense_top_k": s.settings.dense_top_k,
                "sparse_top_k": s.settings.sparse_top_k,
                "rrf_k": s.settings.rrf_k,
                "rerank_threshold": s.settings.rerank_threshold,
                "final_top_n": s.settings.final_top_n,
            },
        }

    # --- Courses -------------------------------------------------------------

    @app.get("/api/courses")
    def list_courses() -> list[dict[str, Any]]:
        return svc().db.list_courses()

    @app.post("/api/courses", status_code=201)
    def create_course(body: CourseCreate) -> dict[str, Any]:
        return svc().db.create_course(body.name, body.code)

    @app.post("/api/courses/sample", status_code=201)
    def create_sample_course() -> dict[str, Any]:
        """A ready-made course (two lectures, a slide deck, a scanned page) for first-time users."""
        from course_notes_rag.samples import SAMPLE_COURSE, build_samples

        s = svc()
        files = build_samples(s.settings.storage_dir / "samples")
        course = s.db.create_course(SAMPLE_COURSE["name"], SAMPLE_COURSE["code"])
        docs = [s.ingestion.submit_upload(course["id"], name, path.read_bytes())
                for name, path in files.items()]
        return {"course": s.db.get_course(course["id"]), "documents": docs}

    @app.get("/api/courses/{course_id}")
    def get_course(course_id: str) -> dict[str, Any]:
        return svc().db.get_course(course_id)

    @app.delete("/api/courses/{course_id}", status_code=204)
    def delete_course(course_id: str) -> None:
        svc().ingestion.delete_course(course_id)

    # --- Documents -----------------------------------------------------------

    @app.get("/api/courses/{course_id}/documents")
    def list_documents(course_id: str) -> list[dict[str, Any]]:
        return svc().db.list_documents(course_id)

    @app.post("/api/courses/{course_id}/documents", status_code=202)
    async def upload_documents(course_id: str, files: list[UploadFile] = File(...)) -> dict[str, Any]:
        s = svc()
        s.db.get_course(course_id)
        if not files:
            raise HTTPException(400, "No files were uploaded")
        created, errors = [], []
        for upload in files:
            data = await upload.read()
            try:
                doc = await run_in_threadpool(
                    s.ingestion.submit_upload, course_id, upload.filename or "document.pdf", data
                )
                created.append(doc)
            except UploadError as exc:
                if len(files) == 1:
                    raise
                errors.append({"filename": upload.filename, "detail": str(exc),
                               "status": exc.status_code})
        return {"documents": created, "errors": errors}

    @app.get("/api/documents/{document_id}")
    def get_document(document_id: str) -> dict[str, Any]:
        return svc().db.get_document(document_id)

    @app.get("/api/documents/{document_id}/file")
    def document_file(document_id: str) -> FileResponse:
        doc = svc().db.get_document(document_id)
        path = Path(doc["file_path"])
        if not path.exists():
            raise HTTPException(404, "The PDF file is missing from storage")
        return FileResponse(path, media_type="application/pdf", filename=doc["pdf_name"],
                            content_disposition_type="inline")

    @app.get("/api/documents/{document_id}/markdown")
    def document_markdown(document_id: str) -> dict[str, Any]:
        doc = svc().db.get_document(document_id, with_markdown=True)
        if doc["status"] in ("queued", "parsing"):
            raise HTTPException(409, "The document has not been parsed yet")
        return {"document_id": document_id, "pdf_name": doc["pdf_name"],
                "engine": doc["engine"], "markdown": doc.get("markdown") or ""}

    @app.post("/api/documents/{document_id}/reprocess", status_code=202)
    def reprocess_document(document_id: str) -> dict[str, Any]:
        s = svc()
        doc = s.db.get_document(document_id)
        if doc["status"] not in ("ready", "failed"):
            raise HTTPException(409, "The document is still being processed")
        s.db.update_document(document_id, status="queued", progress=0.0, error=None)
        s.ingestion.enqueue(document_id)
        return s.db.get_document(document_id)

    @app.delete("/api/documents/{document_id}", status_code=204)
    def delete_document(document_id: str) -> None:
        svc().ingestion.delete_document(document_id)

    # --- Retrieval & chat ----------------------------------------------------

    @app.post("/api/search")
    def search(body: SearchRequest) -> dict[str, Any]:
        s = svc()
        s.db.get_course(body.course_id)
        result = s.retriever.retrieve(
            body.query, body.course_id, document_ids=body.document_ids, top_n=body.top_n,
            expansion=body.expansion,
        )
        return result.to_dict()

    @app.post("/api/chat")
    async def chat(body: ChatRequest) -> StreamingResponse:
        s = svc()
        course = s.db.get_course(body.course_id)
        events = s.chat.stream_answer(
            body.course_id, course["code"] or course["name"], body.question,
            history=[t.model_dump() for t in body.history], document_ids=body.document_ids,
            expansion=body.expansion,
        )
        first = await run_in_threadpool(next, events)  # raises request errors before streaming
        return StreamingResponse(
            _sse(itertools.chain([first], events)), media_type="text/event-stream",
            headers=SSE_HEADERS,
        )

    # --- Quizzes & mastery ---------------------------------------------------

    @app.post("/api/quiz")
    async def create_quiz(body: QuizCreate) -> StreamingResponse:
        s = svc()
        req = QuizRequest(
            course_id=body.course_id, topic=body.topic, num_questions=body.num_questions,
            bloom_levels=list(dict.fromkeys(body.bloom_levels)), document_ids=body.document_ids,
            adaptive=body.adaptive, seed=body.seed,
        )
        events = s.quiz.generate(req)
        first = await run_in_threadpool(next, events)
        return StreamingResponse(
            _sse(itertools.chain([first], events)), media_type="text/event-stream",
            headers=SSE_HEADERS,
        )

    @app.get("/api/courses/{course_id}/quizzes")
    def list_quizzes(course_id: str) -> list[dict[str, Any]]:
        s = svc()
        s.db.get_course(course_id)
        return s.db.list_quizzes(course_id)

    @app.get("/api/quizzes/{quiz_id}")
    def get_quiz(quiz_id: str) -> dict[str, Any]:
        return svc().quiz.get_quiz(quiz_id)

    @app.post("/api/quizzes/{quiz_id}/questions/{question_id}/answer")
    def answer(quiz_id: str, question_id: int, body: AnswerSubmit) -> dict[str, Any]:
        return svc().quiz.answer(quiz_id, question_id, body.label)

    @app.get("/api/courses/{course_id}/mastery")
    def mastery(course_id: str) -> dict[str, Any]:
        s = svc()
        s.db.get_course(course_id)
        return s.mastery.course_summary(course_id)

    # --- Frontend ------------------------------------------------------------

    dist = cfg.frontend_dist
    if dist.exists() and (dist / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            if path.startswith("api/"):
                raise HTTPException(404, "Not found")
            candidate = (dist / path).resolve()
            if path and candidate.is_file() and dist.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")

    return app


app = create_app()
