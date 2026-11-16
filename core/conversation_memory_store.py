"""Owner-scoped SQLite memory for conversation context.

Memory is opt-in: callers must inject this store into ConversationService and
explicitly call remember(). Retrieved text is untrusted reference data.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone
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
            columns = {row["name"] for row in db.execute("PRAGMA table_info(conversation_memories)")}
            if "expires_at" not in columns:
                try:
                    db.execute("ALTER TABLE conversation_memories ADD COLUMN expires_at TEXT")
                except sqlite3.OperationalError as exc:
                    # Another process may have completed the same additive
                    # migration after our PRAGMA snapshot.
                    refreshed = {
                        row["name"] for row in db.execute("PRAGMA table_info(conversation_memories)")
                    }
                    if "expires_at" not in refreshed:
                        raise exc
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_conversation_memories_owner_created
                ON conversation_memories(owner_id, created_at DESC, memory_id)
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_conversation_memories_expiry
                ON conversation_memories(expires_at) WHERE expires_at IS NOT NULL
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

    def remember(
        self, *, owner_id: str, content: str, category: str = "note",
        expires_in_seconds: int | None = None,
    ) -> dict[str, Any]:
        owner = self._owner(owner_id)
        if expires_in_seconds is not None and (
            type(expires_in_seconds) is not int or not 60 <= expires_in_seconds <= 315_360_000
        ):
            raise ValueError("expires_in_seconds must be None or an integer from 60 to 315360000.")
        expires_at = (
            (datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds)).isoformat()
            if expires_in_seconds is not None else None
        )
        if not isinstance(content, str) or not content.strip() or len(content.strip()) > self.MAX_TEXT_CHARS:
            raise ValueError(f"content must be 1-{self.MAX_TEXT_CHARS} characters.")
        if not isinstance(category, str) or category not in self.CATEGORIES:
            raise ValueError("category must be note, preference, or lesson.")
        import uuid
        memory_id = uuid.uuid4().hex
        with self._connection() as db:
            # Serialize expiry cleanup and capacity checks across workers.
            db.execute("BEGIN IMMEDIATE")
            now = datetime.now(timezone.utc).isoformat()
            db.execute(
                "DELETE FROM conversation_memories WHERE owner_id = ? "
                "AND expires_at IS NOT NULL AND expires_at <= ?",
                (owner, now),
            )
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
                "INSERT INTO conversation_memories(memory_id, owner_id, category, content, expires_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (memory_id, owner, category, content.strip(), expires_at),
            )
            row = db.execute(
                "SELECT memory_id, category, content, created_at, expires_at FROM conversation_memories "
                "WHERE owner_id = ? AND memory_id = ?", (owner, memory_id)
            ).fetchone()
        return dict(row)

    @staticmethod
    def _tokens(text: str) -> set[str]:
        """Tokenize Unicode words, including unspaced CJK text.

        CJK scripts commonly omit spaces between words. Add character bigrams
        for CJK runs so a query term can match inside a longer saved sentence.
        """
        tokens: set[str] = set()
        cjk = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
                         r"\u3040-\u30ff\u31f0-\u31ff"
                         r"\uac00-\ud7af]")
        for word in re.findall(r"[\w]+", text.casefold(), flags=re.UNICODE):
            if cjk.search(word):
                if len(word) >= 2:
                    tokens.update(word[index:index + 2] for index in range(len(word) - 1))
            elif len(word) >= 2:
                tokens.add(word)
        return tokens

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
            # The store has a hard per-owner cap, so ranking the complete active
            # corpus is bounded and avoids silently missing older relevant facts.
            rows = db.execute(
                "SELECT memory_id, category, content, created_at, expires_at FROM conversation_memories "
                "WHERE owner_id = ? AND (expires_at IS NULL OR expires_at > ?) "
                "ORDER BY memory_id ASC",
                (owner, datetime.now(timezone.utc).isoformat()),
            ).fetchall()

        documents = []
        document_frequency = {token: 0 for token in query_tokens}
        for row in rows:
            item = dict(row)
            tokens = self._tokens(item["content"])
            for token in query_tokens.intersection(tokens):
                document_frequency[token] += 1
            documents.append((item, tokens))

        # A compact BM25-style lexical ranker: rare query terms carry more
        # weight, repeated occurrences have diminishing returns, and a direct
        # phrase match receives a small boost. No embeddings or external calls.
        import math
        corpus_size = max(1, len(documents))
        average_length = sum(len(tokens) for _, tokens in documents) / corpus_size
        normalized_query = " ".join(re.findall(r"[\\w]+", query.casefold(), flags=re.UNICODE))
        ranked = []
        for item, tokens in documents:
            matched = query_tokens.intersection(tokens)
            if not matched:
                continue
            length_norm = len(tokens) / max(1.0, average_length)
            score = 0.0
            for token in matched:
                cjk = re.compile(r"[\\u3400-\\u4dbf\\u4e00-\\u9fff\\uf900-\\ufaff"
                                 r"\\u3040-\\u30ff\\u31f0-\\u31ff"
                                 r"\\uac00-\\ud7af]")
                frequency = sum(
                    word.count(token) if cjk.search(word) else int(word == token)
                    for word in re.findall(r"[\\w]+", item["content"].casefold(), flags=re.UNICODE)
                )
                inverse_frequency = math.log(
                    1.0 + (corpus_size - document_frequency[token] + 0.5)
                    / (document_frequency[token] + 0.5)
                )
                denominator = frequency + 1.2 * (0.25 + 0.75 * length_norm)
                score += inverse_frequency * (frequency * 2.2 / max(0.01, denominator))
            normalized_content = " ".join(re.findall(
                r"[\\w]+", item["content"].casefold(), flags=re.UNICODE
            ))
            if normalized_query and normalized_query in normalized_content:
                score += 1.0
            ranked.append((score, item))
        ranked.sort(
            key=lambda pair: (-pair[0], pair[1]["created_at"], pair[1]["memory_id"]),
        )
        return [{"score": round(score, 6), **item} for score, item in ranked[:limit]]

    def list(self, *, owner_id: str, limit: int = 50) -> list[dict[str, Any]]:
        owner = self._owner(owner_id)
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200.")
        with self._connection() as db:
            rows = db.execute(
                "SELECT memory_id, category, content, created_at, expires_at FROM conversation_memories "
                "WHERE owner_id = ? AND (expires_at IS NULL OR expires_at > ?) "
                "ORDER BY created_at DESC, memory_id DESC LIMIT ?",
                (owner, datetime.now(timezone.utc).isoformat(), limit),
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

    
    def prune_expired(self, *, owner_id: str | None = None) -> int:
        """Permanently delete expired memories, optionally limited to one owner."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as db:
            if owner_id is None:
                cursor = db.execute(
                    "DELETE FROM conversation_memories WHERE expires_at IS NOT NULL AND expires_at <= ?",
                    (now,),
                )
            else:
                owner = self._owner(owner_id)
                cursor = db.execute(
                    "DELETE FROM conversation_memories WHERE owner_id = ? "
                    "AND expires_at IS NOT NULL AND expires_at <= ?",
                    (owner, now),
                )
        return cursor.rowcount
