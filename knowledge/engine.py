"""Local, source-attributed knowledge store for Yuki.

Knowledge documents are stored separately from personal memory. This module uses
only the Python standard library and SQLite; it never calls an AI API.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class KnowledgeResult:
    chunk_id: int
    source_id: str
    source_name: str
    source_path: str
    content: str
    score: float


class KnowledgeEngine:
    """Ingest approved workspace text files and retrieve source-attributed chunks."""

    SUPPORTED_SUFFIXES = frozenset({".txt", ".md", ".rst", ".py", ".json", ".yaml", ".yml"})
    EXTRACTED_SUFFIXES = SUPPORTED_SUFFIXES | frozenset({".html", ".htm", ".pdf", ".docx"})
    MAX_FILE_BYTES = 2_000_000
    MAX_DOCUMENT_CHARS = 200_000
    MAX_CHUNK_CHARS = 2_000
    CHUNK_OVERLAP = 200
    MAX_SEARCH_RESULTS = 20

    def __init__(self, database_path: str | Path = "YukiData/knowledge.sqlite3"):
        self.database_path = Path(database_path).expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            str(self.database_path), timeout=10.0, isolation_level=None
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    @contextmanager
    def _session(self):
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._lock, self._session() as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS knowledge_sources (
                    source_id TEXT PRIMARY KEY,
                    source_name TEXT NOT NULL,
                    source_path TEXT NOT NULL UNIQUE,
                    content_sha256 TEXT NOT NULL,
                    indexed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    chunk_count INTEGER NOT NULL CHECK(chunk_count >= 0)
                );
                CREATE TABLE IF NOT EXISTS knowledge_chunks (
                    chunk_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT NOT NULL REFERENCES knowledge_sources(source_id)
                        ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    UNIQUE(source_id, ordinal)
                );
                CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_source
                    ON knowledge_chunks(source_id, ordinal);
            """)
            try:
                db.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_chunks_fts
                    USING fts5(content, source_name, source_path,
                               tokenize='unicode61')
                """)
            except sqlite3.OperationalError as exc:
                raise RuntimeError(
                    "This Python SQLite build must include FTS5 support."
                ) from exc

    @staticmethod
    def _chunks(text: str) -> list[str]:
        chunks = []
        start = 0
        while start < len(text):
            end = min(start + KnowledgeEngine.MAX_CHUNK_CHARS, len(text))
            if end < len(text):
                boundary = text.rfind("\n", start + 1000, end)
                if boundary > start:
                    end = boundary
            piece = text[start:end].strip()
            if piece:
                chunks.append(piece)
            if end >= len(text):
                break
            start = max(start + 1, end - KnowledgeEngine.CHUNK_OVERLAP)
        return chunks

    @staticmethod
    def _safe_workspace_file(
        path: str | Path, workspace: str | Path, *, allow_extracted_formats: bool = False
    ) -> tuple[Path, Path]:
        workspace_path = Path(workspace).expanduser()
        root = workspace_path.resolve(strict=True)
        supplied = Path(path).expanduser()
        candidate = supplied if supplied.is_absolute() else root / supplied

        # Inspect the caller's lexical path before resolving it so a symlink
        # that points back inside the workspace cannot disappear during
        # canonicalization. Relative paths are rooted at the canonical root.
        lexical_relative = None
        if not supplied.is_absolute():
            try:
                lexical_relative = candidate.relative_to(root)
            except ValueError as exc:
                raise ValueError("Knowledge source must be inside the approved workspace.") from exc
        else:
            try:
                lexical_relative = candidate.absolute().relative_to(workspace_path.absolute())
            except ValueError:
                # Windows short/long path aliases and macOS /var -> /private/var
                # can differ lexically; canonical containment below remains authoritative.
                lexical_relative = None

        if lexical_relative is not None:
            cursor = root
            for part in lexical_relative.parts:
                cursor = cursor / part
                if cursor.is_symlink():
                    raise ValueError("Symbolic links are not permitted as knowledge sources.")

        try:
            resolved = candidate.resolve(strict=True)
            relative = resolved.relative_to(root)
        except (ValueError, OSError) as exc:
            raise ValueError("Knowledge source must be inside the approved workspace.") from exc

        if not resolved.is_file():
            raise ValueError("Knowledge source must be a regular file inside the workspace.")
        allowed_suffixes = (
            KnowledgeEngine.EXTRACTED_SUFFIXES
            if allow_extracted_formats else KnowledgeEngine.SUPPORTED_SUFFIXES
        )
        if resolved.suffix.lower() not in allowed_suffixes:
            raise ValueError("Unsupported file type for this ingestion path.")
        if resolved.stat().st_size > KnowledgeEngine.MAX_FILE_BYTES:
            raise ValueError("Knowledge source exceeds the 2 MB file limit.")
        return root, resolved

    @classmethod
    def chunk_text(cls, text: str) -> list[str]:
        """Return the canonical chunk sequence without mutating the knowledge store."""
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text must be non-empty.")
        if len(text.strip()) > cls.MAX_DOCUMENT_CHARS:
            raise ValueError("Document exceeds the character limit.")
        chunks = cls._chunks(text.strip())
        if not chunks:
            raise ValueError("Document contains no indexable text.")
        return chunks

    def ingest_file(self, path: str | Path, *, workspace: str | Path) -> dict:
        """Index one explicitly selected file; re-ingesting unchanged content is a no-op."""
        _, source = self._safe_workspace_file(path, workspace)
        raw = source.read_bytes()
        try:
            text = raw.decode("utf-8-sig").strip()
        except UnicodeDecodeError as exc:
            raise ValueError("Knowledge source must be valid UTF-8 text.") from exc
        if not text:
            raise ValueError("Knowledge source is empty.")
        if len(text) > self.MAX_DOCUMENT_CHARS:
            raise ValueError("Knowledge source exceeds the 200,000 character limit.")

        digest = hashlib.sha256(raw).hexdigest()
        source_path = str(source)
        source_id = hashlib.sha256(source_path.encode("utf-8")).hexdigest()
        chunks = self._chunks(text)
        if not chunks:
            raise ValueError("Knowledge source contains no indexable text.")

        with self._lock, self._session() as db:
            existing = db.execute(
                "SELECT content_sha256, chunk_count FROM knowledge_sources WHERE source_id=?",
                (source_id,),
            ).fetchone()
            if existing and existing["content_sha256"] == digest:
                return {
                    "source_id": source_id, "source_name": source.name,
                    "chunks_indexed": existing["chunk_count"], "unchanged": True,
                }

            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("DELETE FROM knowledge_chunks_fts WHERE source_path=?", (source_path,))
                db.execute("DELETE FROM knowledge_sources WHERE source_id=?", (source_id,))
                db.execute(
                    """INSERT INTO knowledge_sources
                       (source_id, source_name, source_path, content_sha256, chunk_count)
                       VALUES (?, ?, ?, ?, ?)""",
                    (source_id, source.name, source_path, digest, len(chunks)),
                )
                for ordinal, chunk in enumerate(chunks):
                    cursor = db.execute(
                        "INSERT INTO knowledge_chunks(source_id, ordinal, content) VALUES (?, ?, ?)",
                        (source_id, ordinal, chunk),
                    )
                    db.execute(
                        """INSERT INTO knowledge_chunks_fts(rowid, content, source_name, source_path)
                           VALUES (?, ?, ?, ?)""",
                        (cursor.lastrowid, chunk, source.name, source_path),
                    )
                db.commit()
            except Exception:
                db.rollback()
                raise
        return {
            "source_id": source_id, "source_name": source.name,
            "chunks_indexed": len(chunks), "unchanged": False,
        }

    def search(self, query: str, *, limit: int = 5) -> list[KnowledgeResult]:
        if not isinstance(query, str) or not query.strip() or len(query) > 2000:
            raise ValueError("query must be non-empty text of at most 2000 characters.")
        if type(limit) is not int or not 1 <= limit <= self.MAX_SEARCH_RESULTS:
            raise ValueError(f"limit must be between 1 and {self.MAX_SEARCH_RESULTS}.")
        terms = re.findall(r"[\w-]{2,}", query.casefold(), flags=re.UNICODE)
        if not terms:
            return []
        match = " AND ".join('"' + term.replace('"', '""') + '"' for term in dict.fromkeys(terms))
        with self._lock, self._session() as db:
            rows = db.execute(
                """SELECT c.chunk_id, c.source_id, s.source_name, s.source_path,
                          c.content, bm25(knowledge_chunks_fts) AS rank
                   FROM knowledge_chunks_fts f
                   JOIN knowledge_chunks c ON c.chunk_id = f.rowid
                   JOIN knowledge_sources s ON s.source_id = c.source_id
                   WHERE knowledge_chunks_fts MATCH ?
                   ORDER BY rank, c.chunk_id LIMIT ?""",
                (match, limit),
            ).fetchall()
        return [
            KnowledgeResult(
                chunk_id=row["chunk_id"], source_id=row["source_id"],
                source_name=row["source_name"], source_path=row["source_path"],
                content=row["content"], score=float(row["rank"]),
            )
            for row in rows
        ]

    def get_chunks(self, chunk_ids: list[int]) -> list[KnowledgeResult]:
        """Resolve candidate IDs against canonical indexed source rows."""
        if not isinstance(chunk_ids, (list, tuple)) or len(chunk_ids) > 100:
            raise ValueError("chunk_ids must contain at most 100 IDs.")
        if any(type(item) is not int or item <= 0 for item in chunk_ids):
            raise ValueError("chunk IDs must be positive integers.")
        if not chunk_ids:
            return []
        placeholders = ",".join("?" for _ in chunk_ids)
        with self._lock, self._session() as db:
            rows = db.execute(
                f"""SELECT c.chunk_id, c.source_id, s.source_name, s.source_path,
                           c.content
                    FROM knowledge_chunks c
                    JOIN knowledge_sources s ON s.source_id = c.source_id
                    WHERE c.chunk_id IN ({placeholders})""",
                tuple(chunk_ids),
            ).fetchall()
        by_id = {
            row["chunk_id"]: KnowledgeResult(
                chunk_id=row["chunk_id"], source_id=row["source_id"],
                source_name=row["source_name"], source_path=row["source_path"],
                content=row["content"], score=0.0,
            )
            for row in rows
        }
        return [by_id[item] for item in chunk_ids if item in by_id]

    def ingest_extracted_text(
        self, *, source_path: str | Path, workspace: str | Path,
        source_sha256: str, text: str,
    ) -> dict:
        """Index text extracted by a trusted parser from an approved workspace file."""
        _, source = self._safe_workspace_file(
            source_path, workspace, allow_extracted_formats=True
        )
        raw = source.read_bytes()
        if not isinstance(source_sha256, str) or hashlib.sha256(raw).hexdigest() != source_sha256:
            raise ValueError("Source changed after parsing; parse it again before indexing.")
        if not isinstance(text, str):
            raise ValueError("Extracted text must be a string.")
        normalized = text.strip()
        if not normalized:
            raise ValueError("Document contains no indexable text.")
        if len(normalized) > self.MAX_DOCUMENT_CHARS:
            raise ValueError("Extracted document exceeds the 200,000 character limit.")

        source_path_value = str(source)
        source_id = hashlib.sha256(source_path_value.encode("utf-8")).hexdigest()
        chunks = self._chunks(normalized)
        digest = source_sha256
        with self._lock, self._session() as db:
            existing = db.execute(
                "SELECT content_sha256, chunk_count FROM knowledge_sources WHERE source_id=?",
                (source_id,),
            ).fetchone()
            if existing and existing["content_sha256"] == digest:
                return {"source_id": source_id, "source_name": source.name,
                        "chunks_indexed": existing["chunk_count"], "unchanged": True}
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("DELETE FROM knowledge_chunks_fts WHERE source_path=?", (source_path_value,))
                db.execute("DELETE FROM knowledge_sources WHERE source_id=?", (source_id,))
                db.execute(
                    """INSERT INTO knowledge_sources
                       (source_id, source_name, source_path, content_sha256, chunk_count)
                       VALUES (?, ?, ?, ?, ?)""",
                    (source_id, source.name, source_path_value, digest, len(chunks)),
                )
                for ordinal, chunk in enumerate(chunks):
                    cursor = db.execute(
                        "INSERT INTO knowledge_chunks(source_id, ordinal, content) VALUES (?, ?, ?)",
                        (source_id, ordinal, chunk),
                    )
                    db.execute(
                        """INSERT INTO knowledge_chunks_fts(rowid, content, source_name, source_path)
                           VALUES (?, ?, ?, ?)""",
                        (cursor.lastrowid, chunk, source.name, source_path_value),
                    )
                db.commit()
            except Exception:
                db.rollback()
                raise
        return {"source_id": source_id, "source_name": source.name,
                "chunks_indexed": len(chunks), "unchanged": False}

    def get_source_chunks(self, source_id: str) -> list[KnowledgeResult]:
        """Return every current chunk for one canonical source in ordinal order."""
        if not isinstance(source_id, str) or not source_id.strip():
            raise ValueError("source_id is required.")
        with self._lock, self._session() as db:
            rows = db.execute(
                """SELECT c.chunk_id, c.source_id, s.source_name, s.source_path, c.content
                   FROM knowledge_chunks c
                   JOIN knowledge_sources s ON s.source_id = c.source_id
                   WHERE c.source_id=? ORDER BY c.ordinal""",
                (source_id,),
            ).fetchall()
        return [
            KnowledgeResult(
                chunk_id=row["chunk_id"], source_id=row["source_id"],
                source_name=row["source_name"], source_path=row["source_path"],
                content=row["content"], score=0.0,
            )
            for row in rows
        ]

    def list_sources(self) -> list[dict]:
        with self._lock, self._session() as db:
            rows = db.execute(
                """SELECT source_id, source_name, source_path, content_sha256,
                          indexed_at, chunk_count
                   FROM knowledge_sources ORDER BY source_name COLLATE NOCASE"""
            ).fetchall()
        return [dict(row) for row in rows]

    def remove_source(self, source_id: str) -> bool:
        if not isinstance(source_id, str) or not source_id.strip():
            raise ValueError("source_id is required.")
        with self._lock, self._session() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    "SELECT source_path FROM knowledge_sources WHERE source_id=?",
                    (source_id,),
                ).fetchone()
                if row is None:
                    db.commit()
                    return False
                db.execute(
                    "DELETE FROM knowledge_chunks_fts WHERE source_path=?",
                    (row["source_path"],),
                )
                db.execute("DELETE FROM knowledge_sources WHERE source_id=?", (source_id,))
                db.commit()
                return True
            except Exception:
                db.rollback()
                raise
