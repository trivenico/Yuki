"""Owner-scoped SQLite memory for conversation context.

Memory is opt-in: callers must inject this store into ConversationService and
explicitly call remember(). Retrieved text is untrusted reference data.
"""
from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from core.sqlite_file_security import prepare_private_sqlite_path


class ConversationMemoryStore:
    MAX_TEXT_CHARS = 2000
    MAX_ENTRIES_PER_OWNER = 2000
    CATEGORIES = frozenset({"note", "preference", "lesson"})

    def __init__(self, database_path: str | Path):
        if not isinstance(database_path, (str, Path)) or not str(database_path).strip():
            raise ValueError("database_path must be a non-empty path.")
        self.database_path = prepare_private_sqlite_path(database_path)
        with self._connection() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS conversation_memories (
                    memory_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    category TEXT NOT NULL CHECK(category IN ('note', 'preference', 'lesson')),
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    CHECK(length(content) BETWEEN 1 AND 2000)
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_conversation_memories_owner_created
                ON conversation_memories(owner_id, created_at DESC, memory_id)
            """)

    @staticmethod
    def _owner(owner_id: str) -> str:
        if not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 200:
            raise ValueError("owner_id must be non-empty text of at most 200 characters.")
        return owner_id.strip()

    @staticmethod
    def _connect(path: str):
        db = sqlite3.connect(path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        return db

    @contextmanager
    def _connection(self):
        db = self._connect(str(self.database_path))
        try:
            with db:
                yield db
        finally:
            db.close()

    def remember(self, *, owner_id: str, content: str, category: str = "note") -> dict[str, Any]:
        owner = self._owner(owner_id)
        if not isinstance(content, str) or not content.strip() or len(content.strip()) > self.MAX_TEXT_CHARS:
            raise ValueError(f"content must be 1-{self.MAX_TEXT_CHARS} characters.")
        if not isinstance(category, str) or category not in self.CATEGORIES:
            raise ValueError("category must be note, preference, or lesson.")
        import uuid
        memory_id = uuid.uuid4().hex
        with self._connection() as db:
            # Serialize per-database capacity checks across independent workers.
            db.execute("BEGIN IMMEDIATE")
            count = db.execute(
                "SELECT count(*) FROM conversation_memories WHERE owner_id = ?", (owner,)
            ).fetchone()[0]
            if count >= self.MAX_ENTRIES_PER_OWNER:
                oldest = db.execute(
                    "SELECT memory_id FROM conversation_memories WHERE owner_id = ? "
                    "ORDER BY created_at ASC, memory_id ASC LIMIT 1", (owner,)
                ).fetchone()
                if oldest is not None:
                    db.execute(
                        "DELETE FROM conversation_memories WHERE owner_id = ? AND memory_id = ?",
                        (owner, oldest["memory_id"]),
                    )
            db.execute(
                "INSERT INTO conversation_memories(memory_id, owner_id, category, content) "
                "VALUES (?, ?, ?, ?)",
                (memory_id, owner, category, content.strip()),
            )
            row = db.execute(
                "SELECT memory_id, category, content, created_at FROM conversation_memories "
                "WHERE owner_id = ? AND memory_id = ?", (owner, memory_id)
            ).fetchone()
        return dict(row)

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return set(re.findall(r"[\\w]{2,}", text.casefold(), flags=re.UNICODE))

    def search(self, *, owner_id: str, query: str, limit: int = 5) -> list[dict[str, Any]]:
        owner = self._owner(owner_id)
        if not isinstance(query, str) or not query.strip() or len(query) > 2000:
            raise ValueError("query must be non-empty text of at most 2000 characters.")
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("limit must be between 1 and 20.")
        query_tokens = self._tokens(query)
        if not query_tokens:
            return []
        with self._connection() as db:
            rows = db.execute(
                "SELECT memory_id, category, content, created_at FROM conversation_memories "
                "WHERE owner_id = ? ORDER BY created_at DESC, memory_id DESC LIMIT 200",
                (owner,),
            ).fetchall()
        ranked = []
        for row in rows:
            item = dict(row)
            tokens = self._tokens(item["content"])
            overlap = len(query_tokens & tokens)
            if overlap:
                score = overlap / max(1, len(query_tokens | tokens))
                ranked.append((score, item))
        ranked.sort(key=lambda item: (item[0], item[1]["created_at"], item[1]["memory_id"]), reverse=True)
        return [{"score": round(score, 4), **item} for score, item in ranked[:limit]]

    def list(self, *, owner_id: str, limit: int = 50) -> list[dict[str, Any]]:
        owner = self._owner(owner_id)
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200.")
        with self._connection() as db:
            rows = db.execute(
                "SELECT memory_id, category, content, created_at FROM conversation_memories "
                "WHERE owner_id = ? ORDER BY created_at DESC, memory_id DESC LIMIT ?",
                (owner, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete(self, *, owner_id: str, memory_id: str) -> bool:
        owner = self._owner(owner_id)
        if not isinstance(memory_id, str) or not memory_id.strip():
            return False
        with self._connection() as db:
            cursor = db.execute(
                "DELETE FROM conversation_memories WHERE owner_id = ? AND memory_id = ?",
                (owner, memory_id.strip()),
            )
        return cursor.rowcount == 1

    def clear(self, *, owner_id: str) -> int:
        owner = self._owner(owner_id)
        with self._connection() as db:
            cursor = db.execute("DELETE FROM conversation_memories WHERE owner_id = ?", (owner,))
        return cursor.rowcount
