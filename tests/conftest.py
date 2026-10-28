from __future__ import annotations

import copy
import hashlib
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from course_notes_rag.config.settings import Settings  # noqa: E402
from course_notes_rag.ingestion.parser import PyMuPDFParser  # noqa: E402
from course_notes_rag.models import BBox, Block, ParsedDocument  # noqa: E402
from course_notes_rag.samples import build_invalid_files, build_samples  # noqa: E402
from course_notes_rag.services import build_services  # noqa: E402

SVM = "Lecture_04_SVMs.pdf"
RNN = "Lecture_07_RNNs.pdf"
SLIDES = "Lecture_02_Probability_Slides.pdf"
SCAN = "Handwritten_Gradient_Descent.pdf"


@pytest.fixture(scope="session")
def sample_dir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("samples")


@pytest.fixture(scope="session")
def sample_pdfs(sample_dir) -> dict[str, Path]:
    return build_samples(sample_dir)


@pytest.fixture(scope="session")
def invalid_pdfs(sample_dir) -> dict[str, Path]:
    return build_invalid_files(sample_dir / "invalid")


@pytest.fixture
def test_settings(tmp_path) -> Settings:
    return Settings(profile="test", storage_dir=tmp_path / "storage", frontend_dist=tmp_path / "nodist")


class CachingParser:
    """Parses each distinct file once per test session (parsing is the slow part)."""

    name = "pymupdf"

    def __init__(self) -> None:
        self.inner = PyMuPDFParser()
        self.cache: dict[str, object] = {}
        self.lock = threading.Lock()

    def parse(self, path, *, pdf_name=None):
        key = hashlib.sha256(Path(path).read_bytes()).hexdigest() if Path(path).exists() else str(path)
        with self.lock:
            if key not in self.cache:
                try:
                    self.cache[key] = self.inner.parse(path)
                except Exception as exc:  # noqa: BLE001 - cache failures too
                    self.cache[key] = exc
            result = self.cache[key]
        if isinstance(result, Exception):
            raise result
        doc = copy.deepcopy(result)
        doc.pdf_name = pdf_name or Path(path).name
        return doc


_PARSER = CachingParser()


@pytest.fixture
def services(test_settings):
    svc = build_services(test_settings, parser=_PARSER)
    yield svc
    svc.close()


def wait_ready(services, doc_id: str, timeout: float = 120.0) -> dict:
    services.ingestion.wait(doc_id, timeout=timeout)
    deadline = time.time() + timeout
    while time.time() < deadline:
        doc = services.db.get_document(doc_id)
        if doc["status"] in ("ready", "failed"):
            return doc
        time.sleep(0.05)
    raise TimeoutError(doc_id)


@pytest.fixture
def ready_course(services, sample_pdfs):
    """A course with the SVM and RNN lectures plus the slide deck fully ingested."""
    course = services.db.create_course("Machine Learning Foundations", "CS229-lite")
    docs = {}
    for name in (SVM, RNN, SLIDES):
        doc = services.ingestion.submit_upload(course["id"], name, sample_pdfs[name].read_bytes())
        docs[name] = wait_ready(services, doc["id"])
        assert docs[name]["status"] == "ready", docs[name]
    return {"course": course, "docs": docs}


def make_doc(blocks: list[Block], *, title: str | None = "Lecture 1: Test", is_slides: bool = False,
             pdf_name: str = "Lecture_01.pdf") -> ParsedDocument:
    pages = max((b.page for b in blocks), default=1)
    return ParsedDocument(pdf_name, pages, [(595.0, 842.0)] * pages, blocks, title=title,
                          is_slides=is_slides, engine="test")


def block(kind: str, text: str, page: int = 1, level: int = 0, y: float = 100.0) -> Block:
    return Block(kind, text, page, BBox(page, 50.0, y, 500.0, y + 20.0), level)
