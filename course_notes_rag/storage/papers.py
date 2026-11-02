"""SQLite tables for past question papers, their questions and generated mock papers.

Lives beside :mod:`course_notes_rag.storage.db` and shares its connection, so foreign keys,
the lock and transactions behave exactly as they do for every other table: deleting a course
removes its papers, and deleting a note section clears the questions mapped to it.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from course_notes_rag.storage.db import Database, NotFoundError, _now, new_id

PAPERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS exam_papers (
    id TEXT PRIMARY KEY,
    course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    pdf_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    title TEXT,
    exam_type TEXT,
    year INTEGER,
    term TEXT,
    max_marks REAL,
    duration TEXT,
    parts TEXT NOT NULL DEFAULT '[]',
    warnings TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL,
    progress REAL NOT NULL DEFAULT 0,
    error TEXT,
    engine TEXT,
    num_pages INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_exam_papers_course ON exam_papers(course_id);
CREATE TABLE IF NOT EXISTS paper_questions (
    id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL REFERENCES exam_papers(id) ON DELETE CASCADE,
    course_id TEXT NOT NULL,
    ord INTEGER NOT NULL,
    number TEXT NOT NULL,
    sub TEXT NOT NULL DEFAULT '',
    label TEXT NOT NULL,
    part TEXT NOT NULL DEFAULT '',
    choice_group TEXT NOT NULL DEFAULT '',
    context TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL,
    marks REAL,
    marks_estimated INTEGER NOT NULL DEFAULT 0,
    page INTEGER NOT NULL DEFAULT 1,
    course_outcome TEXT NOT NULL DEFAULT '',
    question_type TEXT,
    type_source TEXT,
    bloom_level TEXT,
    bloom_source TEXT,
    parent_id TEXT REFERENCES parents(id) ON DELETE SET NULL,
    section_title TEXT,
    lecture_title TEXT,
    notes_pdf TEXT,
    document_id TEXT,
    page_start INTEGER,
    page_end INTEGER,
    topic_score REAL
);
CREATE INDEX IF NOT EXISTS idx_paper_questions_paper ON paper_questions(paper_id);
CREATE INDEX IF NOT EXISTS idx_paper_questions_course ON paper_questions(course_id);
CREATE TABLE IF NOT EXISTS mock_papers (
    id TEXT PRIMARY KEY,
    course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    spec TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mock_papers_course ON mock_papers(course_id);
CREATE TABLE IF NOT EXISTS mock_questions (
    mock_id TEXT NOT NULL REFERENCES mock_papers(id) ON DELETE CASCADE,
    ord INTEGER NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (mock_id, ord)
);
"""

PAPER_FIELDS = {
    "title", "exam_type", "year", "term", "max_marks", "duration", "parts", "warnings", "status",
    "progress", "error", "engine", "num_pages",
}
QUESTION_FIELDS = (
    "number", "sub", "label", "part", "choice_group", "context", "text", "marks",
    "marks_estimated", "page", "course_outcome", "question_type", "type_source", "bloom_level",
    "bloom_source", "parent_id", "section_title", "lecture_title", "notes_pdf", "document_id",
    "page_start", "page_end", "topic_score",
)
CLASSIFICATION_FIELDS = {"question_type", "type_source", "bloom_level", "bloom_source"}
TOPIC_FIELDS = (
    "parent_id", "section_title", "lecture_title", "notes_pdf", "document_id", "page_start",
    "page_end", "topic_score",
)


class PaperStore:
    def __init__(self, db: Database) -> None:
        self.db = db
        with db._lock:
            db._conn.executescript(PAPERS_SCHEMA)

    # --- Papers ------------------------------------------------------------------

    def create_paper(
        self, course_id: str, pdf_name: str, file_path: str, content_hash: str, size_bytes: int
    ) -> dict[str, Any]:
        self.db.get_course(course_id)
        paper_id = new_id()
        now = _now()
        self.db._execute(
            """INSERT INTO exam_papers (id, course_id, pdf_name, file_path, content_hash, size_bytes,
                                        status, progress, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 'queued', 0, ?, ?)""",
            (paper_id, course_id, pdf_name, file_path, content_hash, size_bytes, now, now),
        )
        return self.get_paper(paper_id)

    def get_paper(self, paper_id: str) -> dict[str, Any]:
        rows = self.db._query(_PAPER_SELECT + " WHERE p.id = ? GROUP BY p.id", (paper_id,))
        if not rows:
            raise NotFoundError(f"Paper {paper_id} not found")
        return _paper_row(rows[0])

    def list_papers(self, course_id: str) -> list[dict[str, Any]]:
        self.db.get_course(course_id)
        rows = self.db._query(
            _PAPER_SELECT + " WHERE p.course_id = ? GROUP BY p.id ORDER BY p.year IS NULL, p.year, "
            "p.created_at, p.rowid",
            (course_id,),
        )
        return [_paper_row(r) for r in rows]

    def find_paper_by_hash(self, course_id: str, content_hash: str) -> dict[str, Any] | None:
        rows = self.db._query(
            "SELECT id FROM exam_papers WHERE course_id = ? AND content_hash = ?",
            (course_id, content_hash),
        )
        return self.get_paper(rows[0]["id"]) if rows else None

    def find_paper_by_name(self, course_id: str, pdf_name: str) -> dict[str, Any] | None:
        rows = self.db._query(
            "SELECT id FROM exam_papers WHERE course_id = ? AND lower(pdf_name) = lower(?)",
            (course_id, pdf_name),
        )
        return self.get_paper(rows[0]["id"]) if rows else None

    def update_paper(self, paper_id: str, **fields: Any) -> None:
        unknown = set(fields) - PAPER_FIELDS
        if unknown:
            raise ValueError(f"Unknown paper fields: {sorted(unknown)}")
        if not fields:
            return
        for key in ("parts", "warnings"):
            if key in fields:
                fields[key] = json.dumps(fields[key])
        assignments = ", ".join(f"{k} = ?" for k in fields)
        cursor = self.db._execute(
            f"UPDATE exam_papers SET {assignments}, updated_at = ? WHERE id = ?",
            (*fields.values(), _now(), paper_id),
        )
        if cursor.rowcount == 0:
            raise NotFoundError(f"Paper {paper_id} not found")

    def delete_paper(self, paper_id: str) -> dict[str, Any]:
        paper = self.get_paper(paper_id)
        self.db._execute("DELETE FROM exam_papers WHERE id = ?", (paper_id,))
        return paper

    # --- Questions ---------------------------------------------------------------

    def save_questions(
        self, paper_id: str, course_id: str, questions: list[dict[str, Any]]
    ) -> list[str]:
        """Replace the paper's questions; returns their new ids in order."""
        ids = [new_id() for _ in questions]
        columns = ", ".join(QUESTION_FIELDS)
        marks = ", ".join("?" * len(QUESTION_FIELDS))
        rows = [
            (qid, paper_id, course_id, i, *[q.get(f) for f in QUESTION_FIELDS])
            for i, (qid, q) in enumerate(zip(ids, questions))
        ]
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM paper_questions WHERE paper_id = ?", (paper_id,))
            conn.executemany(
                f"INSERT INTO paper_questions (id, paper_id, course_id, ord, {columns}) "
                f"VALUES (?, ?, ?, ?, {marks})",
                rows,
            )
        return ids

    def list_questions(
        self, *, paper_id: str | None = None, course_id: str | None = None,
        ready_only: bool = False,
    ) -> list[dict[str, Any]]:
        if (paper_id is None) == (course_id is None):
            raise ValueError("pass exactly one of paper_id and course_id")
        sql = "SELECT q.* FROM paper_questions q JOIN exam_papers p ON p.id = q.paper_id WHERE "
        sql += "q.paper_id = ?" if paper_id else "q.course_id = ?"
        if ready_only:
            sql += " AND p.status = 'ready'"
        sql += " ORDER BY p.year IS NULL, p.year, p.created_at, p.rowid, q.ord"
        rows = self.db._query(sql, (paper_id or course_id,))
        return [_question_row(r) for r in rows]

    def update_classification(self, updates: dict[str, dict[str, Any]]) -> None:
        """``{question_id: {question_type, type_source, bloom_level, bloom_source}}``."""
        with self.db.transaction() as conn:
            for question_id, fields in updates.items():
                unknown = set(fields) - CLASSIFICATION_FIELDS
                if unknown:
                    raise ValueError(f"Unknown classification fields: {sorted(unknown)}")
                assignments = ", ".join(f"{k} = ?" for k in fields)
                conn.execute(
                    f"UPDATE paper_questions SET {assignments} WHERE id = ?",
                    (*fields.values(), question_id),
                )

    def update_topics(self, updates: dict[str, dict[str, Any] | None]) -> None:
        """``{question_id: topic}``; ``None`` records that no section of the notes matched."""
        assignments = ", ".join(f"{k} = ?" for k in TOPIC_FIELDS)
        with self.db.transaction() as conn:
            for question_id, topic in updates.items():
                values = [(topic or {}).get(k) for k in TOPIC_FIELDS]
                try:
                    conn.execute(
                        f"UPDATE paper_questions SET {assignments} WHERE id = ?",
                        (*values, question_id),
                    )
                except sqlite3.IntegrityError:  # the section was deleted while mapping ran
                    conn.execute(
                        f"UPDATE paper_questions SET {assignments} WHERE id = ?",
                        (*[None] * len(TOPIC_FIELDS), question_id),
                    )

    # --- Mock papers -------------------------------------------------------------

    def create_mock(self, course_id: str, kind: str, title: str, spec: dict[str, Any]) -> str:
        self.db.get_course(course_id)
        mock_id = new_id()
        self.db._execute(
            "INSERT INTO mock_papers (id, course_id, kind, title, spec, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (mock_id, course_id, kind, title, json.dumps(spec), _now()),
        )
        return mock_id

    def add_mock_question(self, mock_id: str, ord_: int, payload: dict[str, Any]) -> None:
        self.db._execute(
            "INSERT INTO mock_questions (mock_id, ord, payload) VALUES (?, ?, ?)",
            (mock_id, ord_, json.dumps(payload)),
        )

    def get_mock(self, mock_id: str) -> dict[str, Any]:
        rows = self.db._query("SELECT * FROM mock_papers WHERE id = ?", (mock_id,))
        if not rows:
            raise NotFoundError(f"Mock paper {mock_id} not found")
        mock = dict(rows[0])
        mock["spec"] = json.loads(mock["spec"])
        questions = self.db._query(
            "SELECT payload FROM mock_questions WHERE mock_id = ? ORDER BY ord", (mock_id,)
        )
        mock["questions"] = [json.loads(r["payload"]) for r in questions]
        return mock

    def list_mocks(self, course_id: str) -> list[dict[str, Any]]:
        self.db.get_course(course_id)
        rows = self.db._query(
            """SELECT m.id, m.kind, m.title, m.created_at, COUNT(q.ord) AS question_count
               FROM mock_papers m LEFT JOIN mock_questions q ON q.mock_id = m.id
               WHERE m.course_id = ? GROUP BY m.id ORDER BY m.created_at DESC, m.rowid DESC""",
            (course_id,),
        )
        return [dict(r) for r in rows]

    def delete_mock(self, mock_id: str) -> None:
        cursor = self.db._execute("DELETE FROM mock_papers WHERE id = ?", (mock_id,))
        if cursor.rowcount == 0:
            raise NotFoundError(f"Mock paper {mock_id} not found")


_PAPER_SELECT = """SELECT p.*, COUNT(q.id) AS question_count, COALESCE(SUM(q.marks), 0) AS marks_found
                   FROM exam_papers p LEFT JOIN paper_questions q ON q.paper_id = p.id"""


def _paper_row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["parts"] = json.loads(data["parts"])
    data["warnings"] = json.loads(data["warnings"])
    return data


def _question_row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["marks_estimated"] = bool(data["marks_estimated"])
    return data
