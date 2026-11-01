"""Portable, permission-checked collective memory with provenance and version history.

Memory is data, never executable instruction. Callers supply authenticated actor
identity, access policy, and provenance; retrieval is lexical and local-only.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Callable, Iterable, Mapping
from uuid import uuid4

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}$")
_SCOPES = frozenset({"private", "project", "team", "global"})
_CATEGORIES = frozenset({"decision", "fact", "lesson", "pattern", "handoff", "preference"})
_MAX_CONTENT = 12000


class MemoryVaultError(ValueError):
    """Invalid memory operation or record."""


@dataclass(frozen=True)
class MemoryRecord:
    memory_id: str
    scope: str
    scope_id: str
    category: str
    content: str
    source_type: str
    source_ref: str
    source_digest: str
    created_by: str
    version: int
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class MemorySearchResult:
    record: MemoryRecord
    score: float


class CollectiveMemoryVault:
    """SQLite memory vault with append-only versions and fail-closed ACL checks."""

    def __init__(
        self,
        database_path: str,
        *,
        authorize: Callable[[str, str, str], bool],
        max_records: int = 100_000,
    ):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path must be non-empty.")
        if not callable(authorize):
            raise TypeError("a trusted authorization callback is required.")
        if type(max_records) is not int or not 1 <= max_records <= 1_000_000:
            raise ValueError("max_records must be between 1 and 1,000,000.")
        self.database_path = str(Path(database_path).expanduser())
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self.authorize = authorize
        self.max_records = max_records
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS memories (
                    memory_id TEXT PRIMARY KEY,
                    scope TEXT NOT NULL,
                    scope_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_ref TEXT NOT NULL,
                    source_digest TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    deleted_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_memories_scope
                    ON memories(scope, scope_id, updated_at);
                CREATE TABLE IF NOT EXISTS memory_versions (
                    memory_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    category TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_ref TEXT NOT NULL,
                    source_digest TEXT NOT NULL,
                    changed_by TEXT NOT NULL,
                    changed_at TEXT NOT NULL,
                    PRIMARY KEY(memory_id, version),
                    FOREIGN KEY(memory_id) REFERENCES memories(memory_id)
                );
                CREATE TABLE IF NOT EXISTS memory_audit (
                    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    memory_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    occurred_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_memory_audit_memory
                    ON memory_audit(memory_id, audit_id);
            """)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=10.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _allowed(self, actor: str, action: str, resource: str) -> bool:
        try:
            return self.authorize(actor, action, resource) is True
        except Exception:
            return False

    @staticmethod
    def _text(value: str, name: str, limit: int) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
            raise MemoryVaultError(f"{name} must be non-empty bounded text.")
        return value.strip()

    @staticmethod
    def _record(row: sqlite3.Row) -> MemoryRecord:
        return MemoryRecord(
            memory_id=row["memory_id"], scope=row["scope"], scope_id=row["scope_id"],
            category=row["category"], content=row["content"], source_type=row["source_type"],
            source_ref=row["source_ref"], source_digest=row["source_digest"],
            created_by=row["created_by"], version=row["version"],
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    def remember(
        self, *, actor_id: str, scope: str, scope_id: str, category: str,
        content: str, source_type: str, source_ref: str, source_digest: str = "",
        memory_id: str | None = None,
    ) -> MemoryRecord:
        actor = self._text(actor_id, "actor_id", 100)
        sid = self._text(scope_id, "scope_id", 120)
        body = self._text(content, "content", _MAX_CONTENT)
        source_type = self._text(source_type, "source_type", 40)
        source_ref = self._text(source_ref, "source_ref", 500)
        if not isinstance(scope, str) or scope not in _SCOPES or not isinstance(category, str) or category not in _CATEGORIES:
            raise MemoryVaultError("unsupported scope or category.")
        if not _ID.fullmatch(sid):
            raise MemoryVaultError("scope_id must be a bounded identifier.")
        if not isinstance(source_digest, str) or len(source_digest) > 128:
            raise MemoryVaultError("source_digest is invalid.")
        digest = source_digest.strip() or hashlib.sha256(body.encode("utf-8")).hexdigest()
        mid = self._text(memory_id, "memory_id", 100) if memory_id else uuid4().hex
        if not _ID.fullmatch(mid):
            raise MemoryVaultError("memory_id must be a bounded identifier.")
        resource = f"{scope}:{sid}"
        if not self._allowed(actor, "memory:write", resource):
            raise PermissionError("memory write is not authorized.")
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM memories WHERE memory_id=?", (mid,)).fetchone()
            if row and row["deleted_at"] is not None:
                raise MemoryVaultError("deleted memories cannot be overwritten; create a new record.")
            if row:
                if (row["scope"], row["scope_id"]) != (scope, sid):
                    raise MemoryVaultError("memory scope is immutable.")
                if not self._allowed(actor, "memory:update", resource):
                    raise PermissionError("memory update is not authorized.")
                version = row["version"] + 1
                db.execute("""
                    INSERT INTO memory_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (mid, version, body, category, source_type, source_ref, digest, actor, now))
                db.execute("""
                    UPDATE memories SET category=?, content=?, source_type=?, source_ref=?,
                    source_digest=?, version=?, updated_at=? WHERE memory_id=?
                """, (category, body, source_type, source_ref, digest, version, now, mid))
                action = "update"
            else:
                count = db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
                if count >= self.max_records:
                    raise MemoryVaultError("memory vault capacity reached.")
                version = 1
                db.execute("""
                    INSERT INTO memories VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """, (mid, scope, sid, category, body, source_type, source_ref,
                      digest, actor, version, now, now))
                db.execute("""
                    INSERT INTO memory_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (mid, version, body, category, source_type, source_ref, digest, actor, now))
                action = "create"
            db.execute(
                "INSERT INTO memory_audit(memory_id, actor_id, action, occurred_at) VALUES (?, ?, ?, ?)",
                (mid, actor, action, now),
            )
            return self._record(db.execute("SELECT * FROM memories WHERE memory_id=?", (mid,)).fetchone())

    def get(self, *, actor_id: str, memory_id: str) -> MemoryRecord | None:
        actor = self._text(actor_id, "actor_id", 100)
        mid = self._text(memory_id, "memory_id", 100)
        with self._connect() as db:
            row = db.execute("SELECT * FROM memories WHERE memory_id=? AND deleted_at IS NULL", (mid,)).fetchone()
        if not row:
            return None
        resource = f"{row['scope']}:{row['scope_id']}"
        if not self._allowed(actor, "memory:read", resource):
            raise PermissionError("memory read is not authorized.")
        return self._record(row)

    def scoped_candidates(
        self, *, actor_id: str, scopes: Iterable[tuple[str, str]], limit: int = 500,
    ) -> tuple[MemoryRecord, ...]:
        """Return bounded candidates only from explicitly authorized scopes."""
        actor = self._text(actor_id, "actor_id", 100)
        requested = tuple(scopes)
        if not requested or len(requested) > 100:
            raise ValueError("provide between 1 and 100 explicit scopes.")
        if type(limit) is not int or not 1 <= limit <= 5000:
            raise ValueError("limit must be between 1 and 5000.")
        allowed = []
        for item in requested:
            if not isinstance(item, (tuple, list)) or len(item) != 2:
                raise MemoryVaultError("each scope must be a (scope, scope_id) pair.")
            scope, sid = item
            if not isinstance(scope, str) or scope not in _SCOPES or not isinstance(sid, str) or not _ID.fullmatch(sid):
                raise MemoryVaultError("invalid search scope.")
            if self._allowed(actor, "memory:read", f"{scope}:{sid}"):
                allowed.append((scope, sid))
        if not allowed:
            return ()
        placeholders = ",".join("(?, ?)" for _ in allowed)
        params = [part for pair in allowed for part in pair]
        params.append(limit)
        with self._connect() as db:
            rows = db.execute(
                f"""SELECT * FROM memories WHERE deleted_at IS NULL
                AND (scope, scope_id) IN ({placeholders})
                ORDER BY updated_at DESC LIMIT ?""", params
            ).fetchall()
        return tuple(self._record(row) for row in rows)

    def search(
        self, *, actor_id: str, query: str, scopes: Iterable[tuple[str, str]],
        limit: int = 10,
    ) -> tuple[MemorySearchResult, ...]:
        actor = self._text(actor_id, "actor_id", 100)
        query = self._text(query, "query", 1000)
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50.")
        requested = tuple(scopes)
        if not requested or len(requested) > 100:
            raise ValueError("provide between 1 and 100 explicit scopes.")
        allowed = []
        for item in requested:
            if not isinstance(item, (tuple, list)) or len(item) != 2:
                raise MemoryVaultError("each scope must be a (scope, scope_id) pair.")
            scope, sid = item
            if not isinstance(scope, str) or scope not in _SCOPES or not isinstance(sid, str) or not _ID.fullmatch(sid):
                raise MemoryVaultError("invalid search scope.")
            if self._allowed(actor, "memory:read", f"{scope}:{sid}"):
                allowed.append((scope, sid))
        if not allowed:
            return ()
        terms = set(re.findall(r"[a-z0-9_]{2,}", query.casefold()))
        if not terms:
            return ()
        placeholders = ",".join("(?, ?)" for _ in allowed)
        params = [part for pair in allowed for part in pair]
        with self._connect() as db:
            rows = db.execute(
                f"SELECT * FROM memories WHERE deleted_at IS NULL AND (scope, scope_id) IN ({placeholders})",
                params,
            ).fetchall()
        ranked = []
        for row in rows:
            tokens = set(re.findall(r"[a-z0-9_]{2,}", row["content"].casefold()))
            overlap = len(terms & tokens)
            if overlap:
                score = overlap / max(1, len(terms | tokens))
                ranked.append(MemorySearchResult(self._record(row), round(score, 4)))
        ranked.sort(key=lambda result: (result.score, result.record.updated_at), reverse=True)
        return tuple(ranked[:limit])

    def history(self, *, actor_id: str, memory_id: str) -> tuple[dict, ...]:
        actor = self._text(actor_id, "actor_id", 100)
        record = self.get(actor_id=actor, memory_id=memory_id)
        if record is None:
            return ()
        resource = f"{record.scope}:{record.scope_id}"
        if not self._allowed(actor, "memory:history", resource):
            raise PermissionError("memory history is not authorized.")
        with self._connect() as db:
            rows = db.execute(
                """SELECT version, category, content, source_type, source_ref,
                source_digest, changed_by, changed_at FROM memory_versions
                WHERE memory_id=? ORDER BY version""", (memory_id,)
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def forget(self, *, actor_id: str, memory_id: str) -> bool:
        actor = self._text(actor_id, "actor_id", 100)
        record = self.get(actor_id=actor, memory_id=memory_id)
        if record is None:
            return False
        resource = f"{record.scope}:{record.scope_id}"
        if not self._allowed(actor, "memory:delete", resource):
            raise PermissionError("memory deletion is not authorized.")
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute(
                "UPDATE memories SET deleted_at=?, updated_at=? WHERE memory_id=? AND deleted_at IS NULL",
                (now, now, memory_id),
            ).rowcount
            if changed:
                db.execute(
                    "INSERT INTO memory_audit(memory_id, actor_id, action, occurred_at) VALUES (?, ?, 'delete', ?)",
                    (memory_id, actor, now),
                )
            return bool(changed)

    def export_records(self, *, actor_id: str, scopes: Iterable[tuple[str, str]]) -> bytes:
        actor = self._text(actor_id, "actor_id", 100)
        records = []
        requested = tuple(scopes)
        if not requested or len(requested) > 100:
            raise ValueError("provide between 1 and 100 explicit export scopes.")
        for item in requested:
            if not isinstance(item, (tuple, list)) or len(item) != 2:
                raise MemoryVaultError("each export scope must be a (scope, scope_id) pair.")
            scope, sid = item
            if scope not in _SCOPES or not isinstance(sid, str) or not _ID.fullmatch(sid):
                raise MemoryVaultError("invalid export scope.")
            resource = f"{scope}:{sid}"
            if not self._allowed(actor, "memory:export", resource):
                raise PermissionError("memory export is not authorized.")
            with self._connect() as db:
                rows = db.execute(
                    "SELECT * FROM memories WHERE scope=? AND scope_id=? AND deleted_at IS NULL ORDER BY memory_id",
                    (scope, sid),
                ).fetchall()
            records.extend(dict(row) for row in rows)
        payload = {"schema_version": 1, "exported_at": datetime.now(timezone.utc).isoformat(),
                   "records": records}
        return (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")

    def import_records(self, *, actor_id: str, payload: bytes) -> int:
        actor = self._text(actor_id, "actor_id", 100)
        if not isinstance(payload, bytes) or len(payload) > 10_000_000:
            raise MemoryVaultError("import must be bytes no larger than 10 MB.")
        try:
            data = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MemoryVaultError("invalid memory export.") from exc
        if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("records"), list):
            raise MemoryVaultError("unsupported memory export schema.")
        if len(data["records"]) > self.max_records:
            raise MemoryVaultError("import exceeds vault capacity.")
        imported = 0
        for item in data["records"]:
            if not isinstance(item, dict):
                raise MemoryVaultError("invalid memory export record.")
            self.remember(
                actor_id=actor, scope=item["scope"], scope_id=item["scope_id"],
                category=item["category"], content=item["content"],
                source_type=item["source_type"], source_ref=item["source_ref"],
                source_digest=item["source_digest"],
            )
            imported += 1
        return imported
