"""Consent-gated, source-attributed conversation memory for Yuki.

This stores approved conversation excerpts for retrieval; it never trains a
model, executes imported content, or treats imported text as instructions.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
from typing import Callable, Iterable

_SOURCE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_MAX_TEXT = 12_000
_MAX_SOURCE_ID = 200
_MAX_RECORDS = 2_000


@dataclass(frozen=True)
class ConversationMemory:
    memory_id: int
    owner_id: str
    source: str
    source_record_id: str
    role: str
    content: str
    content_sha256: str
    created_at: str
    imported_at: str


class ConversationMemoryStore:
    """SQLite memory with explicit consent, owner isolation and deletion controls."""

    def __init__(
        self, database_path: str, *,
        authorize: Callable[[str, str, str], bool],
        embedding_fn: Callable[[str], Iterable[float]] | None = None,
    ) -> None:
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path must be a non-empty path.")
        if not callable(authorize):
            raise TypeError("a trusted authorization callback is required.")
        self.database_path = str(Path(database_path).expanduser())
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self.authorize = authorize
        if embedding_fn is not None and not callable(embedding_fn):
            raise TypeError("embedding_fn must be callable.")
        self.embedding_fn = embedding_fn
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS conversation_memories (
                    memory_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_record_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    imported_at TEXT NOT NULL,
                    UNIQUE(owner_id, source, source_record_id)
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_conversation_memory_owner
                ON conversation_memories(owner_id, memory_id)
            """)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=5000")
            db.execute("PRAGMA synchronous=FULL")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _allowed(self, actor_id: str, action: str, owner_id: str) -> bool:
        if not isinstance(actor_id, str) or not actor_id.strip() or len(actor_id) > 200:
            return False
        try:
            return self.authorize(actor_id.strip(), action, owner_id) is True
        except Exception:
            return False

    @staticmethod
    def _row(row: sqlite3.Row) -> ConversationMemory:
        return ConversationMemory(
            row["memory_id"], row["owner_id"], row["source"],
            row["source_record_id"], row["role"], row["content"],
            row["content_sha256"], row["created_at"], row["imported_at"],
        )

    def import_records(
        self, *, actor_id: str, owner_id: str, source: str,
        records: Iterable[dict], consent: bool,
    ) -> int:
        """Import explicitly consented, normalized records; return inserted count."""
        if consent is not True:
            raise PermissionError("explicit consent is required before importing history.")
        if not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 200:
            raise ValueError("owner_id must be non-empty bounded text.")
        if not isinstance(source, str) or not _SOURCE.fullmatch(source):
            raise ValueError("source must be a bounded provider identifier.")
        if not self._allowed(actor_id, "memory:import", owner_id):
            raise PermissionError("conversation memory import is not authorized.")
        if isinstance(records, (str, bytes, dict)):
            raise ValueError("records must be an iterable of normalized record objects.")
        batch = []
        for record in records:
            if len(batch) >= _MAX_RECORDS:
                raise ValueError("an import batch may contain at most 2000 records.")
            if not isinstance(record, dict):
                raise ValueError("each record must be an object.")
            record_id = record.get("id")
            role = record.get("role")
            content = record.get("content")
            created_at = record.get("created_at", "")
            if not isinstance(record_id, str) or not record_id.strip() or len(record_id) > _MAX_SOURCE_ID:
                raise ValueError("each record needs a bounded stable source id.")
            if not isinstance(role, str) or role not in {"user", "assistant", "system", "tool"}:
                raise ValueError("record role is not supported.")
            if not isinstance(content, str) or not content.strip() or len(content) > _MAX_TEXT or "\x00" in content:
                raise ValueError("record content must be bounded non-empty text.")
            if not isinstance(created_at, str) or len(created_at) > 64:
                raise ValueError("created_at must be bounded text.")
            normalized = content.strip()
            digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            batch.append((owner_id.strip(), source, record_id.strip(), role,
                          normalized, digest, created_at,
                          datetime.now(timezone.utc).isoformat()))
        inserted = 0
        with self._connect() as db:
            for row in batch:
                cursor = db.execute("""
                    INSERT OR IGNORE INTO conversation_memories(
                        owner_id, source, source_record_id, role, content,
                        content_sha256, created_at, imported_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, row)
                inserted += cursor.rowcount
        return inserted

    def search(
        self, *, actor_id: str, owner_id: str, query: str, limit: int = 10,
    ) -> list[ConversationMemory]:
        if not self._allowed(actor_id, "memory:read", owner_id):
            raise PermissionError("conversation memory read is not authorized.")
        if not isinstance(query, str) or not query.strip() or len(query) > 500:
            raise ValueError("query must be non-empty bounded text.")
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("limit must be an integer from 1 to 50.")
        # instr() performs literal substring matching without LIKE wildcard
        # interpretation or cross-platform SQL ESCAPE quoting differences.
        with self._connect() as db:
            rows = db.execute("""
                SELECT * FROM conversation_memories
                WHERE owner_id=? AND instr(lower(content), lower(?)) > 0
                ORDER BY memory_id DESC LIMIT ?
            """, (owner_id, query.strip(), limit)).fetchall()
        return [self._row(row) for row in rows]

    @staticmethod
    def _normalized_vector(value: Iterable[float]) -> tuple[float, ...] | None:
        try:
            vector = tuple(float(component) for component in value)
        except (TypeError, ValueError, OverflowError):
            return None
        if not vector or len(vector) > 16384 or any(not math.isfinite(x) for x in vector):
            return None
        norm = math.sqrt(sum(x * x for x in vector))
        return tuple(x / norm for x in vector) if norm else None

    def semantic_search(self, *, actor_id: str, owner_id: str, query: str, limit: int = 10) -> list[ConversationMemory]:
        """Search this owner's records with an injected local embedder and lexical fallback."""
        if self.embedding_fn is None:
            return self.search(actor_id=actor_id, owner_id=owner_id, query=query, limit=limit)
        if not self._allowed(actor_id, "memory:read", owner_id):
            raise PermissionError("conversation memory read is not authorized.")
        if not isinstance(query, str) or not query.strip() or len(query) > 500:
            raise ValueError("query must be non-empty bounded text.")
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("limit must be an integer from 1 to 50.")
        try:
            query_vector = self._normalized_vector(self.embedding_fn(query.strip()))
        except Exception:
            query_vector = None
        if query_vector is None:
            return self.search(actor_id=actor_id, owner_id=owner_id, query=query, limit=limit)
        with self._connect() as db:
            rows = db.execute("SELECT * FROM conversation_memories WHERE owner_id=? ORDER BY memory_id DESC LIMIT ?", (owner_id, _MAX_RECORDS)).fetchall()
        ranked = []
        for row in rows:
            try:
                vector = self._normalized_vector(self.embedding_fn(row["content"]))
            except Exception:
                vector = None
            if vector is None or len(vector) != len(query_vector):
                continue
            score = sum(a * b for a, b in zip(query_vector, vector))
            if score > 0:
                ranked.append((score, self._row(row)))
        ranked.sort(key=lambda item: (item[0], item[1].memory_id), reverse=True)
        if ranked:
            return [item for _, item in ranked[:limit]]
        return self.search(actor_id=actor_id, owner_id=owner_id, query=query, limit=limit)
    def delete(
        self, *, actor_id: str, owner_id: str, memory_id: int,
    ) -> bool:
        if not self._allowed(actor_id, "memory:delete", owner_id):
            raise PermissionError("conversation memory deletion is not authorized.")
        if type(memory_id) is not int or memory_id < 1:
            raise ValueError("memory_id must be a positive integer.")
        with self._connect() as db:
            cursor = db.execute(
                "DELETE FROM conversation_memories WHERE owner_id=? AND memory_id=?",
                (owner_id, memory_id),
            )
        return cursor.rowcount == 1

    def clear_owner(self, *, actor_id: str, owner_id: str) -> int:
        if not self._allowed(actor_id, "memory:delete_all", owner_id):
            raise PermissionError("clearing conversation memory is not authorized.")
        with self._connect() as db:
            cursor = db.execute(
                "DELETE FROM conversation_memories WHERE owner_id=?", (owner_id,),
            )
        return cursor.rowcount

    def count(self, *, actor_id: str, owner_id: str) -> int:
        if not self._allowed(actor_id, "memory:read", owner_id):
            raise PermissionError("conversation memory read is not authorized.")
        with self._connect() as db:
            row = db.execute(
                "SELECT COUNT(*) AS total FROM conversation_memories WHERE owner_id=?",
                (owner_id,),
            ).fetchone()
        return int(row["total"])
