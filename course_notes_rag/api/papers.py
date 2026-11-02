"""HTTP routes for past question papers, the exam blueprint, the revision plan and mock papers."""

from __future__ import annotations

import itertools
from collections.abc import Callable, Iterable, Iterator
from typing import Any, Literal

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from course_notes_rag.ingestion.pipeline import UploadError
from course_notes_rag.papers.mock import MAX_PRACTICE_QUESTIONS, MockRequest, MockRequestError
from course_notes_rag.papers.plan import MAX_DAYS
from course_notes_rag.papers.service import PaperRequestError
from course_notes_rag.papers.taxonomy import QuestionType


class PaperUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    year: int | None = Field(default=None, ge=1950, le=2100)
    exam_type: str | None = Field(default=None, max_length=40)


class MockCreate(BaseModel):
    course_id: str
    kind: Literal["paper", "practice"] = "practice"
    num_questions: int = Field(default=6, ge=1, le=MAX_PRACTICE_QUESTIONS)
    question_types: list[QuestionType] | None = None
    topic: str | None = Field(default=None, max_length=300)
    template_paper_id: str | None = None
    seed: int | None = None


def register_paper_routes(
    app: FastAPI, svc: Callable[[], Any], sse: Callable[[Iterable[Any]], Iterator[str]],
    sse_headers: dict[str, str],
) -> None:
    async def _bad_request(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    app.add_exception_handler(PaperRequestError, _bad_request)
    app.add_exception_handler(MockRequestError, _bad_request)

    @app.get("/api/courses/{course_id}/papers")
    def list_papers(course_id: str) -> list[dict[str, Any]]:
        return svc().papers.list_papers(course_id)

    @app.post("/api/courses/{course_id}/papers", status_code=202)
    async def upload_papers(course_id: str, files: list[UploadFile] = File(...)) -> dict[str, Any]:
        s = svc()
        s.db.get_course(course_id)
        created, errors = [], []
        for upload in files:
            data = await upload.read()
            try:
                created.append(await run_in_threadpool(
                    s.papers.submit_upload, course_id, upload.filename or "paper.pdf", data
                ))
            except UploadError as exc:
                if len(files) == 1:
                    raise
                errors.append({"filename": upload.filename, "detail": str(exc),
                               "status": exc.status_code})
        return {"papers": created, "errors": errors}

    @app.post("/api/courses/{course_id}/papers/sample", status_code=202)
    def add_sample_papers(course_id: str) -> dict[str, Any]:
        s = svc()
        return s.papers.add_sample_papers(course_id, s.settings.storage_dir / "samples" / "papers")

    @app.post("/api/courses/{course_id}/papers/remap", status_code=202)
    def remap_papers(course_id: str) -> dict[str, Any]:
        return svc().papers.remap(course_id)

    @app.get("/api/courses/{course_id}/papers/blueprint")
    def blueprint(course_id: str) -> dict[str, Any]:
        s = svc()
        s.db.get_course(course_id)
        return s.papers.blueprint(course_id)

    @app.get("/api/courses/{course_id}/papers/plan")
    def plan(course_id: str, days: int | None = Query(default=None, ge=1, le=MAX_DAYS)) -> dict[str, Any]:
        s = svc()
        s.db.get_course(course_id)
        return s.papers.plan(course_id, days)

    @app.get("/api/courses/{course_id}/mocks")
    def list_mocks(course_id: str) -> list[dict[str, Any]]:
        return svc().papers.store.list_mocks(course_id)

    @app.post("/api/papers/mock")
    async def create_mock(body: MockCreate) -> StreamingResponse:
        req = MockRequest(
            course_id=body.course_id, kind=body.kind, num_questions=body.num_questions,
            question_types=list(body.question_types) if body.question_types else None,
            topic=body.topic, template_paper_id=body.template_paper_id, seed=body.seed,
        )
        events = svc().papers.generate_mock(req)
        first = await run_in_threadpool(next, events)  # raises request errors before streaming
        return StreamingResponse(
            sse(itertools.chain([first], events)), media_type="text/event-stream",
            headers=sse_headers,
        )

    @app.get("/api/mocks/{mock_id}")
    def get_mock(mock_id: str) -> dict[str, Any]:
        return svc().papers.store.get_mock(mock_id)

    @app.delete("/api/mocks/{mock_id}", status_code=204)
    def delete_mock(mock_id: str) -> None:
        svc().papers.store.delete_mock(mock_id)

    @app.get("/api/papers/{paper_id}")
    def get_paper(paper_id: str) -> dict[str, Any]:
        return svc().papers.get_paper(paper_id)

    @app.patch("/api/papers/{paper_id}")
    def update_paper(paper_id: str, body: PaperUpdate) -> dict[str, Any]:
        return svc().papers.update_paper(paper_id, body.model_dump(exclude_unset=True))

    @app.delete("/api/papers/{paper_id}", status_code=204)
    def delete_paper(paper_id: str) -> None:
        svc().papers.delete_paper(paper_id)

    @app.post("/api/papers/{paper_id}/reprocess", status_code=202)
    def reprocess_paper(paper_id: str) -> dict[str, Any]:
        return svc().papers.reprocess(paper_id)

    @app.get("/api/papers/{paper_id}/file")
    def paper_file(paper_id: str) -> FileResponse:
        path, name = svc().papers.file_path(paper_id)
        if not path.exists():
            raise HTTPException(404, "The PDF file is missing from storage")
        return FileResponse(path, media_type="application/pdf", filename=name,
                            content_disposition_type="inline")
