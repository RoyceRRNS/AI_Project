"""Past papers end to end: upload, background reading and labelling, and everything built on them."""

from __future__ import annotations

import hashlib
import logging
import shutil
import threading
from collections.abc import Iterator
from concurrent.futures import Future
from pathlib import Path
from typing import Any

from course_notes_rag.generation.llm import LLMClient
from course_notes_rag.generation.quiz_engine import QuizEvent
from course_notes_rag.ingestion.parser import DocumentParser, ParseError
from course_notes_rag.ingestion.pipeline import (
    PDF_MAGIC, IngestionService, UploadError, safe_filename,
)
from course_notes_rag.papers.blueprint import build_blueprint
from course_notes_rag.papers.classify import QuestionClassifier
from course_notes_rag.papers.extract import read_paper
from course_notes_rag.papers.mock import MockGenerator, MockRequest
from course_notes_rag.papers.plan import build_plan
from course_notes_rag.papers.samples import build_sample_papers
from course_notes_rag.retrieval.encoders import Embedder
from course_notes_rag.storage.db import Database, NotFoundError
from course_notes_rag.storage.papers import PaperStore

log = logging.getLogger(__name__)

EDITABLE_FIELDS = {"title", "year", "exam_type"}


class PaperRequestError(ValueError):
    """The request cannot be served, for a reason the student can act on."""


class PaperService:
    def __init__(
        self, db: Database, llm: LLMClient, parser: DocumentParser, retriever: Any,
        embedder: Embedder, ingestion: IngestionService, papers_dir: Path, *,
        mastery_initial: float = 0.5, max_upload_mb: int = 100, max_retries: int = 2,
        temperature: float = 0.4,
    ) -> None:
        self.db = db
        self.store = PaperStore(db)
        self.llm = llm
        self.parser = parser
        self.embedder = embedder
        self.ingestion = ingestion
        self.papers_dir = Path(papers_dir)
        self.mastery_initial = mastery_initial
        self.max_bytes = max_upload_mb * 1024 * 1024
        self.classifier = QuestionClassifier(llm, retriever)
        self.mock = MockGenerator(
            db, self.store, llm, retriever, max_retries=max_retries, temperature=temperature
        )
        self._futures: dict[str, Future] = {}
        self._lock = threading.Lock()
        self._blueprints: dict[str, tuple[Any, dict[str, Any]]] = {}

    # --- Upload --------------------------------------------------------------------

    def submit_upload(self, course_id: str, filename: str, data: bytes) -> dict[str, Any]:
        self.db.get_course(course_id)
        if not data:
            raise UploadError(f"{filename or 'File'} is empty")
        if len(data) > self.max_bytes:
            raise UploadError(
                f"{filename} is larger than {self.max_bytes // (1024 * 1024)} MB", status_code=413
            )
        if not data[:1024].lstrip().startswith(PDF_MAGIC):
            raise UploadError(f"{filename} is not a PDF file", status_code=415)
        digest = hashlib.sha256(data).hexdigest()
        existing = self.store.find_paper_by_hash(course_id, digest)
        if existing is not None:
            raise UploadError(
                f"{filename} is already in this course as {existing['pdf_name']}",
                status_code=409, existing=existing,
            )
        name = safe_filename(filename)
        if self.store.find_paper_by_name(course_id, name) is not None:
            stem, suffix = Path(name).stem, Path(name).suffix
            n = 2
            while self.store.find_paper_by_name(course_id, f"{stem} ({n}){suffix}") is not None:
                n += 1
            name = f"{stem} ({n}){suffix}"
        target_dir = self.papers_dir / course_id
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{digest[:16]}_{name}"
        path.write_bytes(data)
        paper = self.store.create_paper(course_id, name, str(path), digest, len(data))
        self.enqueue(paper["id"])
        return paper

    def add_sample_papers(self, course_id: str, samples_dir: Path) -> dict[str, Any]:
        self.db.get_course(course_id)
        papers, skipped = [], []
        for name, path in build_sample_papers(samples_dir).items():
            try:
                papers.append(self.submit_upload(course_id, name, path.read_bytes()))
            except UploadError as exc:
                if exc.status_code != 409:
                    raise
                skipped.append(name)
        return {"papers": papers, "skipped": skipped}

    def enqueue(self, paper_id: str) -> Future:
        with self._lock:
            future = self.ingestion.run_job(self.process, paper_id)
            self._futures[paper_id] = future
            return future

    def wait(self, paper_id: str, timeout: float | None = None) -> None:
        with self._lock:
            future = self._futures.get(paper_id)
        if future is not None:
            future.result(timeout=timeout)

    # --- Processing ----------------------------------------------------------------

    def process(self, paper_id: str) -> None:
        try:
            self._process(paper_id)
        except NotFoundError:  # deleted while it was being read
            return
        except Exception as exc:  # noqa: BLE001 - any failure is reported on the paper
            log.exception("paper processing failed for %s", paper_id)
            message = str(exc) or exc.__class__.__name__
            try:
                self.store.update_paper(paper_id, status="failed", error=message[:500])
            except NotFoundError:
                pass

    def _process(self, paper_id: str) -> None:
        paper = self.store.get_paper(paper_id)
        course_id = paper["course_id"]
        self.store.update_paper(paper_id, status="parsing", progress=0.1, error=None)
        parsed = self.parser.parse(
            Path(paper["file_path"]), pdf_name=paper["pdf_name"], keep_markers=True
        )
        if not parsed.blocks:
            raise ParseError("No text could be read from this PDF")
        self.store.update_paper(
            paper_id, status="extracting", progress=0.4, engine=parsed.engine,
            num_pages=parsed.num_pages,
        )
        extracted = read_paper(parsed, self.llm)
        if not extracted.questions:
            raise ParseError("No exam questions were found in this PDF")
        records = [q.record() for q in extracted.questions]
        ids = self.store.save_questions(paper_id, course_id, records)
        self.store.update_paper(
            paper_id, status="classifying", progress=0.6, title=extracted.title,
            exam_type=extracted.exam_type, year=extracted.year, term=extracted.term,
            max_marks=extracted.max_marks, duration=extracted.duration,
            parts=[p.to_dict() for p in extracted.parts], warnings=extracted.warnings,
        )
        labelled = [{**record, "id": qid} for qid, record in zip(ids, records)]
        self.store.update_classification(self.classifier.classify(labelled))
        self.store.update_paper(paper_id, status="mapping", progress=0.75)
        self.store.update_topics(self.classifier.map_topics(labelled, course_id))
        self.store.update_paper(paper_id, status="ready", progress=1.0)

    def remap(self, course_id: str) -> dict[str, Any]:
        """Match the questions against the notes again, after the notes have changed."""
        queued = 0
        for paper in self.store.list_papers(course_id):
            if paper["status"] != "ready":
                continue
            self.store.update_paper(paper["id"], status="mapping", progress=0.75)
            with self._lock:
                self._futures[paper["id"]] = self.ingestion.run_job(self._remap_paper, paper["id"])
            queued += 1
        return {"queued": queued}

    def _remap_paper(self, paper_id: str) -> None:
        try:
            paper = self.store.get_paper(paper_id)
            questions = self.store.list_questions(paper_id=paper_id)
            self.store.update_topics(self.classifier.map_topics(questions, paper["course_id"]))
            self.store.update_paper(paper_id, status="ready", progress=1.0)
        except NotFoundError:
            return
        except Exception as exc:  # noqa: BLE001 - the earlier labels are still good
            log.exception("re-mapping failed for %s", paper_id)
            self.store.update_paper(paper_id, status="ready", progress=1.0,
                                    warnings=[f"Topic re-mapping failed: {exc}"])

    # --- Reading -------------------------------------------------------------------

    def list_papers(self, course_id: str) -> list[dict[str, Any]]:
        return [_public(p) for p in self.store.list_papers(course_id)]

    def get_paper(self, paper_id: str) -> dict[str, Any]:
        paper = _public(self.store.get_paper(paper_id))
        paper["questions"] = self.store.list_questions(paper_id=paper_id)
        return paper

    def file_path(self, paper_id: str) -> tuple[Path, str]:
        paper = self.store.get_paper(paper_id)
        return Path(paper["file_path"]), paper["pdf_name"]

    def update_paper(self, paper_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        unknown = set(fields) - EDITABLE_FIELDS
        if unknown:
            raise PaperRequestError(f"These fields cannot be edited: {sorted(unknown)}")
        self.store.update_paper(paper_id, **fields)
        return _public(self.store.get_paper(paper_id))

    def reprocess(self, paper_id: str) -> dict[str, Any]:
        paper = self.store.get_paper(paper_id)
        if paper["status"] not in ("ready", "failed"):
            raise PaperRequestError("The paper is still being processed")
        self.store.update_paper(paper_id, status="queued", progress=0.0, error=None)
        self.enqueue(paper_id)
        return _public(self.store.get_paper(paper_id))

    def delete_paper(self, paper_id: str) -> None:
        paper = self.store.delete_paper(paper_id)
        try:
            Path(paper["file_path"]).unlink(missing_ok=True)
        except OSError:
            log.warning("could not delete %s", paper["file_path"])

    def delete_course_files(self, course_id: str) -> None:
        shutil.rmtree(self.papers_dir / course_id, ignore_errors=True)
        self._blueprints.pop(course_id, None)

    # --- Blueprint, plan, mock -------------------------------------------------------

    def _ready(self, course_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        papers = [p for p in self.store.list_papers(course_id) if p["status"] == "ready"]
        return papers, self.store.list_questions(course_id=course_id, ready_only=True)

    def blueprint(self, course_id: str) -> dict[str, Any]:
        papers, questions = self._ready(course_id)
        # Clustering embeds every question, so reuse the result until something changes.
        key = (
            tuple((p["id"], p["title"], p["year"], p["exam_type"]) for p in papers),
            tuple((q["id"], q["parent_id"], q["question_type"]) for q in questions),
        )
        with self._lock:
            cached = self._blueprints.get(course_id)
        if cached and cached[0] == key:
            return cached[1]
        blueprint = build_blueprint(papers, questions, self.embedder)
        with self._lock:
            self._blueprints[course_id] = (key, blueprint)
        return blueprint

    def plan(self, course_id: str, days: int | None = None) -> dict[str, Any]:
        mastery = {
            row["parent_id"]: row for row in self.db.course_mastery(course_id, self.mastery_initial)
        }
        try:
            return build_plan(self.blueprint(course_id), mastery, days=days)
        except ValueError as exc:
            raise PaperRequestError(str(exc)) from exc

    def generate_mock(self, req: MockRequest) -> Iterator[QuizEvent]:
        course = self.db.get_course(req.course_id)
        blueprint = self.blueprint(req.course_id)
        _papers, questions = self._ready(req.course_id)
        yield from self.mock.generate(
            req, blueprint, questions, course["code"] or course["name"]
        )


def _public(paper: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in paper.items() if k not in ("file_path", "content_hash")}
