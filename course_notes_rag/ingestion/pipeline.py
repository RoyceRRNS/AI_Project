"""Asynchronous ingestion: parse -> chunk -> embed/index, with status tracking per document."""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

from course_notes_rag.ingestion.chunker import HierarchicalChunker
from course_notes_rag.ingestion.parser import DocumentParser, ParseError
from course_notes_rag.retrieval.indexer import CourseNotesIndexer
from course_notes_rag.storage.db import Database, NotFoundError

log = logging.getLogger(__name__)

PDF_MAGIC = b"%PDF-"


class UploadError(ValueError):
    def __init__(self, message: str, status_code: int = 400, existing: dict | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.existing = existing


def safe_filename(name: str) -> str:
    base = Path(name or "").name.strip()
    base = re.sub(r"[^\w.\- ()]+", "_", base)
    if not base.lower().endswith(".pdf"):
        base = f"{base or 'document'}.pdf"
    return base[:180]


class IngestionService:
    def __init__(
        self, db: Database, parser: DocumentParser, chunker: HierarchicalChunker,
        indexer: CourseNotesIndexer, uploads_dir: Path, *, max_upload_mb: int = 100,
        workers: int = 1,
    ) -> None:
        self.db = db
        self.parser = parser
        self.chunker = chunker
        self.indexer = indexer
        self.uploads_dir = uploads_dir
        self.max_bytes = max_upload_mb * 1024 * 1024
        # One worker: parsing and embedding share the GPU, so jobs run one at a time.
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ingest")
        self._futures: dict[str, Future] = {}
        self._lock = threading.Lock()

    # --- Upload --------------------------------------------------------------

    def validate_upload(self, course_id: str, filename: str, data: bytes) -> tuple[str, str]:
        if not data:
            raise UploadError(f"{filename or 'File'} is empty")
        if len(data) > self.max_bytes:
            raise UploadError(
                f"{filename} is larger than {self.max_bytes // (1024 * 1024)} MB", status_code=413
            )
        if not data[:1024].lstrip().startswith(PDF_MAGIC):
            raise UploadError(f"{filename} is not a PDF file", status_code=415)
        digest = hashlib.sha256(data).hexdigest()
        existing = self.db.find_document_by_hash(course_id, digest)
        if existing is not None:
            raise UploadError(
                f"{filename} is already in this course as {existing['pdf_name']}",
                status_code=409, existing=existing,
            )
        return safe_filename(filename), digest

    def submit_upload(self, course_id: str, filename: str, data: bytes) -> dict[str, Any]:
        self.db.get_course(course_id)
        name, digest = self.validate_upload(course_id, filename, data)
        if self.db.find_document_by_name(course_id, name) is not None:
            stem, suffix = Path(name).stem, Path(name).suffix
            n = 2
            while self.db.find_document_by_name(course_id, f"{stem} ({n}){suffix}") is not None:
                n += 1
            name = f"{stem} ({n}){suffix}"
        target_dir = self.uploads_dir / course_id
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{digest[:16]}_{name}"
        path.write_bytes(data)
        doc = self.db.create_document(course_id, name, str(path), digest, len(data))
        self.enqueue(doc["id"])
        return doc

    def enqueue(self, document_id: str) -> Future:
        with self._lock:
            future = self._executor.submit(self.process, document_id)
            self._futures[document_id] = future
            return future

    def wait(self, document_id: str, timeout: float | None = None) -> None:
        with self._lock:
            future = self._futures.get(document_id)
        if future is not None:
            future.result(timeout=timeout)

    # --- Processing ----------------------------------------------------------

    def process(self, document_id: str) -> None:
        try:
            doc = self.db.get_document(document_id)
        except NotFoundError:
            return
        course = self.db.get_course(doc["course_id"])
        try:
            self.db.update_document(document_id, status="parsing", progress=0.1, error=None)
            parsed = self.parser.parse(Path(doc["file_path"]), pdf_name=doc["pdf_name"])
            if not parsed.blocks:
                raise ParseError("No text could be extracted from this PDF")
            self._check_alive(document_id)
            self.db.update_document(
                document_id, status="chunking", progress=0.55, num_pages=parsed.num_pages,
                page_sizes=parsed.page_sizes, is_slides=parsed.is_slides, engine=parsed.engine,
                markdown=parsed.to_markdown(),
            )
            result = self.chunker.chunk(
                parsed, document_id=document_id, course_id=doc["course_id"],
                course_name=course["code"] or course["name"],
            )
            if not result.children:
                raise ParseError("The document produced no searchable text")
            self._check_alive(document_id)
            self.db.save_chunks(document_id, result.parents, result.children)
            self.db.update_document(
                document_id, status="indexing", progress=0.7,
                num_parents=len(result.parents), num_children=len(result.children),
            )
            self.indexer.delete_document(doc["course_id"], document_id)
            self.indexer.index_chunks(result.children)
            self._check_alive(document_id)
            self.db.update_document(document_id, status="ready", progress=1.0)
            self.indexer.sparse.invalidate(doc["course_id"])
        except _Deleted:
            self.indexer.delete_document(doc["course_id"], document_id)
        except Exception as exc:  # noqa: BLE001 - any failure is reported on the document
            log.exception("ingestion failed for %s", document_id)
            message = str(exc) or exc.__class__.__name__
            try:
                self.db.update_document(document_id, status="failed", error=message[:500])
            except NotFoundError:
                pass

    def _check_alive(self, document_id: str) -> None:
        try:
            self.db.get_document(document_id)
        except NotFoundError as exc:
            raise _Deleted from exc

    # --- Deletion ------------------------------------------------------------

    def delete_document(self, document_id: str) -> dict[str, Any]:
        doc = self.db.delete_document(document_id)
        self.indexer.delete_document(doc["course_id"], document_id)
        try:
            Path(doc["file_path"]).unlink(missing_ok=True)
        except OSError:
            log.warning("could not delete %s", doc["file_path"])
        return doc

    def delete_course(self, course_id: str) -> None:
        docs = self.db.list_documents(course_id)
        self.db.delete_course(course_id)
        self.indexer.delete_course(course_id)
        for doc in docs:
            try:
                Path(doc["file_path"]).unlink(missing_ok=True)
            except OSError:
                pass

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)


class _Deleted(Exception):
    """The document was deleted while it was being processed."""
