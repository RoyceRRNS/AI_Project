"""SQLite document store: courses, documents, parent/child chunks, quizzes and mastery."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from course_notes_rag.models import BBox, Block, ChildChunk, ParentChunk

SCHEMA = """
CREATE TABLE IF NOT EXISTS courses (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    code TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    pdf_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    status TEXT NOT NULL,
    progress REAL NOT NULL DEFAULT 0,
    error TEXT,
    num_pages INTEGER,
    page_sizes TEXT,
    is_slides INTEGER NOT NULL DEFAULT 0,
    engine TEXT,
    num_parents INTEGER NOT NULL DEFAULT 0,
    num_children INTEGER NOT NULL DEFAULT 0,
    markdown TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_documents_course ON documents(course_id);
CREATE TABLE IF NOT EXISTS parents (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    course_id TEXT NOT NULL,
    pdf_name TEXT NOT NULL,
    lecture_title TEXT NOT NULL,
    section_title TEXT NOT NULL,
    section_path TEXT NOT NULL,
    blocks TEXT NOT NULL,
    token_count INTEGER NOT NULL,
    page_start INTEGER NOT NULL,
    page_end INTEGER NOT NULL,
    ord INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_parents_course ON parents(course_id);
CREATE INDEX IF NOT EXISTS idx_parents_document ON parents(document_id);
CREATE TABLE IF NOT EXISTS children (
    id TEXT PRIMARY KEY,
    parent_id TEXT NOT NULL REFERENCES parents(id) ON DELETE CASCADE,
    document_id TEXT NOT NULL,
    course_id TEXT NOT NULL,
    pdf_name TEXT NOT NULL,
    section_title TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    contextualized_text TEXT NOT NULL,
    page_num INTEGER NOT NULL,
    page_end INTEGER NOT NULL,
    bboxes TEXT NOT NULL,
    token_count INTEGER NOT NULL,
    ord INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_children_course ON children(course_id);
CREATE INDEX IF NOT EXISTS idx_children_parent ON children(parent_id);
CREATE TABLE IF NOT EXISTS mastery (
    parent_id TEXT PRIMARY KEY REFERENCES parents(id) ON DELETE CASCADE,
    course_id TEXT NOT NULL,
    score REAL NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    correct INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quizzes (
    id TEXT PRIMARY KEY,
    course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    topic TEXT NOT NULL,
    options TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quiz_questions (
    quiz_id TEXT NOT NULL REFERENCES quizzes(id) ON DELETE CASCADE,
    question_id INTEGER NOT NULL,
    parent_id TEXT,
    payload TEXT NOT NULL,
    PRIMARY KEY (quiz_id, question_id)
);
CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quiz_id TEXT NOT NULL REFERENCES quizzes(id) ON DELETE CASCADE,
    question_id INTEGER NOT NULL,
    parent_id TEXT,
    selected_label TEXT NOT NULL,
    is_correct INTEGER NOT NULL,
    attempt_number INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_attempts_quiz ON attempts(quiz_id, question_id);
"""


class NotFoundError(LookupError):
    """Raised when a requested row does not exist."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex


DOCUMENT_FIELDS = {
    "status", "progress", "error", "num_pages", "page_sizes", "is_slides", "engine",
    "num_parents", "num_children", "markdown",
}


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def _query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchall()

    def _execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, tuple(params))

    # --- Courses ---------------------------------------------------------------

    def create_course(self, name: str, code: str | None = None) -> dict[str, Any]:
        name = name.strip()
        if not name:
            raise ValueError("Course name must not be empty")
        course_id = new_id()
        self._execute(
            "INSERT INTO courses (id, name, code, created_at) VALUES (?, ?, ?, ?)",
            (course_id, name, (code or "").strip() or None, _now()),
        )
        return self.get_course(course_id)

    def get_course(self, course_id: str) -> dict[str, Any]:
        rows = self._query(
            """SELECT c.*, COUNT(d.id) AS document_count,
                      SUM(CASE WHEN d.status = 'ready' THEN 1 ELSE 0 END) AS ready_count
               FROM courses c LEFT JOIN documents d ON d.course_id = c.id
               WHERE c.id = ? GROUP BY c.id""",
            (course_id,),
        )
        if not rows:
            raise NotFoundError(f"Course {course_id} not found")
        return _course_row(rows[0])

    def list_courses(self) -> list[dict[str, Any]]:
        rows = self._query(
            """SELECT c.*, COUNT(d.id) AS document_count,
                      SUM(CASE WHEN d.status = 'ready' THEN 1 ELSE 0 END) AS ready_count
               FROM courses c LEFT JOIN documents d ON d.course_id = c.id
               GROUP BY c.id ORDER BY c.created_at, c.name"""
        )
        return [_course_row(r) for r in rows]

    def delete_course(self, course_id: str) -> None:
        self.get_course(course_id)
        with self.transaction() as conn:
            conn.execute(
                "DELETE FROM mastery WHERE course_id = ?", (course_id,)
            )
            conn.execute("DELETE FROM courses WHERE id = ?", (course_id,))

    # --- Documents -------------------------------------------------------------

    def create_document(
        self, course_id: str, pdf_name: str, file_path: str, content_hash: str, size_bytes: int
    ) -> dict[str, Any]:
        self.get_course(course_id)
        doc_id = new_id()
        now = _now()
        self._execute(
            """INSERT INTO documents (id, course_id, pdf_name, file_path, content_hash, size_bytes,
                                      status, progress, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 'queued', 0, ?, ?)""",
            (doc_id, course_id, pdf_name, file_path, content_hash, size_bytes, now, now),
        )
        return self.get_document(doc_id)

    def get_document(self, document_id: str, *, with_markdown: bool = False) -> dict[str, Any]:
        rows = self._query("SELECT * FROM documents WHERE id = ?", (document_id,))
        if not rows:
            raise NotFoundError(f"Document {document_id} not found")
        return _document_row(rows[0], with_markdown=with_markdown)

    def list_documents(self, course_id: str) -> list[dict[str, Any]]:
        self.get_course(course_id)
        rows = self._query(
            "SELECT * FROM documents WHERE course_id = ? ORDER BY created_at, rowid", (course_id,)
        )
        return [_document_row(r) for r in rows]

    def find_document_by_hash(self, course_id: str, content_hash: str) -> dict[str, Any] | None:
        rows = self._query(
            "SELECT * FROM documents WHERE course_id = ? AND content_hash = ?",
            (course_id, content_hash),
        )
        return _document_row(rows[0]) if rows else None

    def find_document_by_name(self, course_id: str, pdf_name: str) -> dict[str, Any] | None:
        rows = self._query(
            "SELECT * FROM documents WHERE course_id = ? AND lower(pdf_name) = lower(?)",
            (course_id, pdf_name),
        )
        return _document_row(rows[0]) if rows else None

    def update_document(self, document_id: str, **fields: Any) -> None:
        unknown = set(fields) - DOCUMENT_FIELDS
        if unknown:
            raise ValueError(f"Unknown document fields: {sorted(unknown)}")
        if not fields:
            return
        if "page_sizes" in fields and fields["page_sizes"] is not None:
            fields["page_sizes"] = json.dumps(fields["page_sizes"])
        if "is_slides" in fields:
            fields["is_slides"] = int(bool(fields["is_slides"]))
        assignments = ", ".join(f"{k} = ?" for k in fields)
        cursor = self._execute(
            f"UPDATE documents SET {assignments}, updated_at = ? WHERE id = ?",
            (*fields.values(), _now(), document_id),
        )
        if cursor.rowcount == 0:
            raise NotFoundError(f"Document {document_id} not found")

    def delete_document(self, document_id: str) -> dict[str, Any]:
        doc = self.get_document(document_id)
        with self.transaction() as conn:
            conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
        return doc

    # --- Chunks ----------------------------------------------------------------

    def save_chunks(
        self, document_id: str, parents: list[ParentChunk], children: list[ChildChunk]
    ) -> None:
        with self.transaction() as conn:
            conn.execute("DELETE FROM parents WHERE document_id = ?", (document_id,))
            conn.executemany(
                """INSERT INTO parents (id, document_id, course_id, pdf_name, lecture_title,
                       section_title, section_path, blocks, token_count, page_start, page_end, ord)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        p.parent_id, p.document_id, p.course_id, p.pdf_name, p.lecture_title,
                        p.section_title, json.dumps(p.section_path),
                        json.dumps([b.to_dict() for b in p.blocks]), p.token_count,
                        p.page_start, p.page_end, p.ord,
                    )
                    for p in parents
                ],
            )
            conn.executemany(
                """INSERT INTO children (id, parent_id, document_id, course_id, pdf_name,
                       section_title, raw_text, contextualized_text, page_num, page_end, bboxes,
                       token_count, ord)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        c.chunk_id, c.parent_id, c.document_id, c.course_id, c.pdf_name,
                        c.section_title, c.raw_text, c.contextualized_text, c.page_num,
                        c.page_end, json.dumps([b.to_dict() for b in c.bboxes]),
                        c.token_count, c.ord,
                    )
                    for c in children
                ],
            )

    def get_parents(self, parent_ids: list[str]) -> dict[str, ParentChunk]:
        if not parent_ids:
            return {}
        placeholders = ",".join("?" * len(parent_ids))
        rows = self._query(f"SELECT * FROM parents WHERE id IN ({placeholders})", parent_ids)
        return {r["id"]: _parent_row(r) for r in rows}

    def list_parents(
        self, course_id: str, document_ids: list[str] | None = None, *, ready_only: bool = True
    ) -> list[ParentChunk]:
        sql = "SELECT p.* FROM parents p JOIN documents d ON d.id = p.document_id WHERE p.course_id = ?"
        params: list[Any] = [course_id]
        if ready_only:
            sql += " AND d.status = 'ready'"
        if document_ids:
            sql += f" AND p.document_id IN ({','.join('?' * len(document_ids))})"
            params.extend(document_ids)
        sql += " ORDER BY d.created_at, p.ord"
        return [_parent_row(r) for r in self._query(sql, params)]

    def list_children(
        self, course_id: str, document_ids: list[str] | None = None, *, ready_only: bool = True
    ) -> list[ChildChunk]:
        sql = "SELECT c.* FROM children c JOIN documents d ON d.id = c.document_id WHERE c.course_id = ?"
        params: list[Any] = [course_id]
        if ready_only:
            sql += " AND d.status = 'ready'"
        if document_ids:
            sql += f" AND c.document_id IN ({','.join('?' * len(document_ids))})"
            params.extend(document_ids)
        sql += " ORDER BY d.created_at, c.ord"
        return [_child_row(r) for r in self._query(sql, params)]

    def get_children(self, chunk_ids: list[str]) -> dict[str, ChildChunk]:
        if not chunk_ids:
            return {}
        placeholders = ",".join("?" * len(chunk_ids))
        rows = self._query(f"SELECT * FROM children WHERE id IN ({placeholders})", chunk_ids)
        return {r["id"]: _child_row(r) for r in rows}

    # --- Mastery ---------------------------------------------------------------

    def get_mastery(self, parent_ids: list[str]) -> dict[str, float]:
        if not parent_ids:
            return {}
        placeholders = ",".join("?" * len(parent_ids))
        rows = self._query(
            f"SELECT parent_id, score FROM mastery WHERE parent_id IN ({placeholders})", parent_ids
        )
        return {r["parent_id"]: float(r["score"]) for r in rows}

    def upsert_mastery(self, parent_id: str, course_id: str, score: float, correct: bool) -> None:
        self._execute(
            """INSERT INTO mastery (parent_id, course_id, score, attempts, correct, updated_at)
               VALUES (?, ?, ?, 1, ?, ?)
               ON CONFLICT(parent_id) DO UPDATE SET
                   score = excluded.score,
                   attempts = mastery.attempts + 1,
                   correct = mastery.correct + excluded.correct,
                   updated_at = excluded.updated_at""",
            (parent_id, course_id, score, int(correct), _now()),
        )

    def course_mastery(self, course_id: str, initial: float) -> list[dict[str, Any]]:
        rows = self._query(
            """SELECT p.id AS parent_id, p.section_title, p.lecture_title, p.pdf_name,
                      p.document_id, p.page_start, p.page_end,
                      COALESCE(m.score, ?) AS score, COALESCE(m.attempts, 0) AS attempts,
                      COALESCE(m.correct, 0) AS correct, m.updated_at
               FROM parents p JOIN documents d ON d.id = p.document_id
               LEFT JOIN mastery m ON m.parent_id = p.id
               WHERE p.course_id = ? AND d.status = 'ready'
               ORDER BY score ASC, attempts DESC, d.created_at, p.ord""",
            (initial, course_id),
        )
        return [dict(r) for r in rows]

    # --- Quizzes ---------------------------------------------------------------

    def create_quiz(self, course_id: str, topic: str, options: dict[str, Any]) -> str:
        self.get_course(course_id)
        quiz_id = new_id()
        self._execute(
            "INSERT INTO quizzes (id, course_id, topic, options, created_at) VALUES (?, ?, ?, ?, ?)",
            (quiz_id, course_id, topic, json.dumps(options), _now()),
        )
        return quiz_id

    def get_quiz(self, quiz_id: str) -> dict[str, Any]:
        rows = self._query("SELECT * FROM quizzes WHERE id = ?", (quiz_id,))
        if not rows:
            raise NotFoundError(f"Quiz {quiz_id} not found")
        row = dict(rows[0])
        row["options"] = json.loads(row["options"])
        return row

    def list_quizzes(self, course_id: str) -> list[dict[str, Any]]:
        rows = self._query(
            """SELECT q.id, q.topic, q.created_at, COUNT(qq.question_id) AS question_count
               FROM quizzes q LEFT JOIN quiz_questions qq ON qq.quiz_id = q.id
               WHERE q.course_id = ? GROUP BY q.id ORDER BY q.created_at DESC""",
            (course_id,),
        )
        return [dict(r) for r in rows]

    def add_quiz_question(
        self, quiz_id: str, question_id: int, parent_id: str | None, payload: dict[str, Any]
    ) -> None:
        self._execute(
            "INSERT INTO quiz_questions (quiz_id, question_id, parent_id, payload) VALUES (?, ?, ?, ?)",
            (quiz_id, question_id, parent_id, json.dumps(payload)),
        )

    def get_quiz_questions(self, quiz_id: str) -> list[dict[str, Any]]:
        rows = self._query(
            "SELECT * FROM quiz_questions WHERE quiz_id = ? ORDER BY question_id", (quiz_id,)
        )
        return [_question_row(r) for r in rows]

    def get_quiz_question(self, quiz_id: str, question_id: int) -> dict[str, Any]:
        rows = self._query(
            "SELECT * FROM quiz_questions WHERE quiz_id = ? AND question_id = ?",
            (quiz_id, question_id),
        )
        if not rows:
            raise NotFoundError(f"Question {question_id} not found in quiz {quiz_id}")
        return _question_row(rows[0])

    def count_attempts(self, quiz_id: str, question_id: int) -> int:
        rows = self._query(
            "SELECT COUNT(*) AS n FROM attempts WHERE quiz_id = ? AND question_id = ?",
            (quiz_id, question_id),
        )
        return int(rows[0]["n"])

    def record_attempt(
        self, quiz_id: str, question_id: int, parent_id: str | None, selected_label: str,
        is_correct: bool, attempt_number: int,
    ) -> None:
        self._execute(
            """INSERT INTO attempts (quiz_id, question_id, parent_id, selected_label, is_correct,
                                     attempt_number, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (quiz_id, question_id, parent_id, selected_label, int(is_correct), attempt_number, _now()),
        )

    def list_attempts(self, quiz_id: str) -> list[dict[str, Any]]:
        rows = self._query(
            "SELECT * FROM attempts WHERE quiz_id = ? ORDER BY id", (quiz_id,)
        )
        return [dict(r) | {"is_correct": bool(r["is_correct"])} for r in rows]


def _course_row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["document_count"] = int(data.get("document_count") or 0)
    data["ready_count"] = int(data.get("ready_count") or 0)
    return data


def _document_row(row: sqlite3.Row, *, with_markdown: bool = False) -> dict[str, Any]:
    data = dict(row)
    data["page_sizes"] = json.loads(data["page_sizes"]) if data.get("page_sizes") else None
    data["is_slides"] = bool(data["is_slides"])
    if not with_markdown:
        data.pop("markdown", None)
    return data


def _parent_row(row: sqlite3.Row) -> ParentChunk:
    return ParentChunk(
        parent_id=row["id"],
        document_id=row["document_id"],
        course_id=row["course_id"],
        pdf_name=row["pdf_name"],
        lecture_title=row["lecture_title"],
        section_title=row["section_title"],
        section_path=json.loads(row["section_path"]),
        blocks=[Block.from_dict(b) for b in json.loads(row["blocks"])],
        token_count=row["token_count"],
        ord=row["ord"],
    )


def _child_row(row: sqlite3.Row) -> ChildChunk:
    return ChildChunk(
        chunk_id=row["id"],
        parent_id=row["parent_id"],
        document_id=row["document_id"],
        course_id=row["course_id"],
        pdf_name=row["pdf_name"],
        section_title=row["section_title"],
        raw_text=row["raw_text"],
        contextualized_text=row["contextualized_text"],
        page_num=row["page_num"],
        page_end=row["page_end"],
        bboxes=[BBox.from_dict(b) for b in json.loads(row["bboxes"])],
        token_count=row["token_count"],
        ord=row["ord"],
    )


def _question_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "quiz_id": row["quiz_id"],
        "question_id": row["question_id"],
        "parent_id": row["parent_id"],
        "payload": json.loads(row["payload"]),
    }
