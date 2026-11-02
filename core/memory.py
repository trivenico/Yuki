"""Actor- and scope-isolated persistent memory for Yuki.

Memory is deliberately separate from conversation event history. Callers must
supply an authenticated actor and an explicit scope (personal or project ID).
No global search API is provided.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import secrets
import sqlite3
from typing import Any


_MAX_CONTENT = 12_000
_MAX_METADATA_BYTES = 8_000
_MAX_QUERY = 500
_CATEGORIES = frozenset({"preference", "fact", "decision", "procedure", "project", "skill", "note"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: str, field: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
        raise ValueError(f"{field} must be non-empty text of at most {limit} characters")
    return value.strip()


def _metadata(value: dict[str, Any] | None) -> str:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("metadata must be an object")
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError("metadata must be JSON serializable") from exc
    if len(raw.encode("utf-8")) > _MAX_METADATA_BYTES:
        raise ValueError("metadata exceeds the configured size limit")
    return raw


@dataclass(frozen=True)
class Memory:
    memory_id: str
    actor_id: str
    scope_id: str
    category: str
    content: str
    source: str
    metadata: dict[str, Any]
    created_at: str
    updated_at: str


class MemoryStore:
    """SQLite memory with mandatory actor + scope predicates on every operation."""

    def __init__(self, database_path: str | Path):
        self.database_path = str(Path(database_path).expanduser())
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("""CREATE TABLE IF NOT EXISTS yuki_memories (
                memory_id TEXT PRIMARY KEY,
                actor_id TEXT NOT NULL,
                scope_id TEXT NOT NULL,
                category TEXT NOT NULL,
                content TEXT NOT NULL,
                source TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""")
            db.execute("""CREATE INDEX IF NOT EXISTS idx_yuki_memory_scope_updated
                ON yuki_memories(actor_id, scope_id, updated_at DESC)""")
            db.execute("""CREATE INDEX IF NOT EXISTS idx_yuki_memory_category
                ON yuki_memories(actor_id, scope_id, category, updated_at DESC)""")

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA synchronous=FULL")
        return _MemoryConnection(db)

    @staticmethod
    def _row(row: sqlite3.Row) -> Memory:
        return Memory(
            row["memory_id"], row["actor_id"], row["scope_id"], row["category"],
            row["content"], row["source"], json.loads(row["metadata_json"]),
            row["created_at"], row["updated_at"],
        )

    def put(self, *, actor_id: str, scope_id: str, category: str, content: str,
            source: str = "user", metadata: dict[str, Any] | None = None,
            memory_id: str | None = None) -> Memory:
        actor = _text(actor_id, "actor_id", 200)
        scope = _text(scope_id, "scope_id", 300)
        category = _text(category, "category", 40).lower()
        if category not in _CATEGORIES:
            raise ValueError("unsupported memory category")
        text = _text(content, "content", _MAX_CONTENT)
        source = _text(source, "source", 120)
        encoded_metadata = _metadata(metadata)
        now = _now()
        if memory_id is None:
            identifier = "ym_" + secrets.token_urlsafe(18)
        else:
            identifier = _text(memory_id, "memory_id", 128)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT * FROM yuki_memories WHERE memory_id=? AND actor_id=? AND scope_id=?",
                (identifier, actor, scope),
            ).fetchone()
            if existing:
                db.execute("""UPDATE yuki_memories SET category=?, content=?, source=?,
                    metadata_json=?, updated_at=? WHERE memory_id=? AND actor_id=? AND scope_id=?""",
                    (category, text, source, encoded_metadata, now, identifier, actor, scope))
            else:
                # A caller cannot overwrite another actor's or scope's identifier.
                collision = db.execute(
                    "SELECT 1 FROM yuki_memories WHERE memory_id=?", (identifier,)
                ).fetchone()
                if collision:
                    raise PermissionError("memory is outside the requested scope")
                db.execute("""INSERT INTO yuki_memories
                    (memory_id,actor_id,scope_id,category,content,source,metadata_json,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                    (identifier, actor, scope, category, text, source, encoded_metadata, now, now))
            row = db.execute(
                "SELECT * FROM yuki_memories WHERE memory_id=? AND actor_id=? AND scope_id=?",
                (identifier, actor, scope),
            ).fetchone()
            return self._row(row)

    def get(self, *, actor_id: str, scope_id: str, memory_id: str) -> Memory:
        actor = _text(actor_id, "actor_id", 200)
        scope = _text(scope_id, "scope_id", 300)
        identifier = _text(memory_id, "memory_id", 128)
        with self._connect() as db:
            row = db.execute("""SELECT * FROM yuki_memories
                WHERE memory_id=? AND actor_id=? AND scope_id=?""",
                (identifier, actor, scope)).fetchone()
        if row is None:
            raise KeyError("memory not found")
        return self._row(row)

    def list(self, *, actor_id: str, scope_id: str, category: str | None = None,
             limit: int = 50) -> list[Memory]:
        actor = _text(actor_id, "actor_id", 200)
        scope = _text(scope_id, "scope_id", 300)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        sql = "SELECT * FROM yuki_memories WHERE actor_id=? AND scope_id=?"
        args: list[Any] = [actor, scope]
        if category is not None:
            cat = _text(category, "category", 40).lower()
            if cat not in _CATEGORIES:
                raise ValueError("unsupported memory category")
            sql += " AND category=?"
            args.append(cat)
        sql += " ORDER BY updated_at DESC, memory_id LIMIT ?"
        args.append(limit)
        with self._connect() as db:
            rows = db.execute(sql, args).fetchall()
        return [self._row(row) for row in rows]

    def search(self, *, actor_id: str, scope_id: str, query: str,
               category: str | None = None, limit: int = 20) -> list[Memory]:
        actor = _text(actor_id, "actor_id", 200)
        scope = _text(scope_id, "scope_id", 300)
        term = _text(query, "query", _MAX_QUERY)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        sql = """SELECT * FROM yuki_memories
            WHERE actor_id=? AND scope_id=? AND content LIKE ? ESCAPE '\\'"""
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        args: list[Any] = [actor, scope, "%" + escaped + "%"]
        if category is not None:
            cat = _text(category, "category", 40).lower()
            if cat not in _CATEGORIES:
                raise ValueError("unsupported memory category")
            sql += " AND category=?"
            args.append(cat)
        sql += " ORDER BY updated_at DESC, memory_id LIMIT ?"
        args.append(limit)
        with self._connect() as db:
            rows = db.execute(sql, args).fetchall()
        return [self._row(row) for row in rows]

    def delete(self, *, actor_id: str, scope_id: str, memory_id: str) -> bool:
        actor = _text(actor_id, "actor_id", 200)
        scope = _text(scope_id, "scope_id", 300)
        identifier = _text(memory_id, "memory_id", 128)
        with self._connect() as db:
            cursor = db.execute("""DELETE FROM yuki_memories
                WHERE memory_id=? AND actor_id=? AND scope_id=?""",
                (identifier, actor, scope))
            return cursor.rowcount == 1


class _MemoryConnection:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self.db

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type:
                self.db.rollback()
            else:
                self.db.commit()
        finally:
            self.db.close()
        return False
