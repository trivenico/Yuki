"""Local SQLite ledger for durable memory conflicts and resolution audit.

The ledger is intentionally local-only. Conflict versions are immutable once
recorded; resolutions are append-only audit events and require an explicit actor.
No network transport or automatic winner selection is implemented here.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterable


_MAX_BATCH = 100
_MAX_VERSION_BYTES = 16 * 1024
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$")


class ConflictLedgerError(RuntimeError):
    """Base error for conflict ledger operations."""


class InvalidConflict(ValueError):
    """Raised when a conflict or resolution fails validation."""


@dataclass(frozen=True)
class ConflictRecord:
    conflict_id: str
    memory_id: str
    versions: tuple[dict[str, Any], ...]
    status: str
    created_at: str
    resolved_version_id: str | None = None
    resolved_by: str | None = None
    resolved_at: str | None = None


def _canonical_json(value: Any, *, max_bytes: int = _MAX_VERSION_BYTES) -> str:
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise InvalidConflict("Value must be JSON serializable.") from exc
    if len(encoded.encode("utf-8")) > max_bytes:
        raise InvalidConflict("Value exceeds the configured size limit.")
    return encoded


def _valid_id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise InvalidConflict(f"{field} must be a safe non-empty identifier.")
    return value


class ConflictLedger:
    """Append-only conflict and resolution records backed by SQLite."""

    def __init__(self, database_path: str | Path):
        if not str(database_path).strip():
            raise ValueError("database_path must be non-empty.")
        self.database_path = str(Path(database_path).expanduser())
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS memory_conflicts (
                    conflict_id TEXT PRIMARY KEY,
                    memory_id TEXT NOT NULL,
                    versions_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    versions_hash TEXT NOT NULL
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS memory_conflict_resolutions (
                    conflict_id TEXT PRIMARY KEY,
                    chosen_version_id TEXT NOT NULL,
                    resolved_by TEXT NOT NULL,
                    resolved_at TEXT NOT NULL,
                    note TEXT NOT NULL,
                    FOREIGN KEY(conflict_id) REFERENCES memory_conflicts(conflict_id)
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS memory_conflict_audit (
                    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conflict_id TEXT NOT NULL,
                    action TEXT NOT NULL CHECK(action IN ('conflict_recorded', 'conflict_resolved')),
                    actor TEXT NOT NULL,
                    event_at TEXT NOT NULL,
                    detail_json TEXT NOT NULL
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_conflict_audit_conflict ON memory_conflict_audit(conflict_id, audit_id)")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _decode(row: sqlite3.Row, resolution: sqlite3.Row | None = None) -> ConflictRecord:
        return ConflictRecord(
            conflict_id=row["conflict_id"],
            memory_id=row["memory_id"],
            versions=tuple(json.loads(row["versions_json"])),
            status="resolved" if resolution else "open",
            created_at=row["created_at"],
            resolved_version_id=resolution["chosen_version_id"] if resolution else None,
            resolved_by=resolution["resolved_by"] if resolution else None,
            resolved_at=resolution["resolved_at"] if resolution else None,
        )

    def record_conflict(
        self,
        *,
        conflict_id: str,
        memory_id: str,
        versions: Iterable[dict[str, Any]],
        created_at: str,
    ) -> ConflictRecord:
        conflict_id = _valid_id(conflict_id, "conflict_id")
        memory_id = _valid_id(memory_id, "memory_id")
        if not isinstance(created_at, str) or not created_at.strip() or len(created_at) > 64:
            raise InvalidConflict("created_at must be a non-empty bounded timestamp string.")
        if isinstance(versions, (str, bytes, dict)):
            raise InvalidConflict("versions must be an iterable of version objects.")
        normalized = []
        for index, version in enumerate(versions):
            if index >= _MAX_BATCH:
                raise InvalidConflict("Conflict contains too many versions.")
            if not isinstance(version, dict):
                raise InvalidConflict("Each version must be an object.")
            version_id = _valid_id(version.get("version_id"), "version_id")
            if any(item["version_id"] == version_id for item in normalized):
                raise InvalidConflict("Version IDs must be unique within a conflict.")
            normalized.append(dict(version))
        if len(normalized) < 2:
            raise InvalidConflict("A conflict must preserve at least two distinct versions.")
        versions_json = _canonical_json(normalized)
        versions_hash = hashlib.sha256(versions_json.encode("utf-8")).hexdigest()
        with self._connect() as db:
            existing = db.execute(
                "SELECT * FROM memory_conflicts WHERE conflict_id=?", (conflict_id,)
            ).fetchone()
            if existing:
                if (existing["memory_id"] != memory_id or
                    existing["versions_hash"] != versions_hash or
                    existing["created_at"] != created_at):
                    raise ConflictLedgerError("Conflict ID already exists with different content.")
                resolution = db.execute(
                    "SELECT * FROM memory_conflict_resolutions WHERE conflict_id=?", (conflict_id,)
                ).fetchone()
                return self._decode(existing, resolution)
            db.execute(
                "INSERT INTO memory_conflicts(conflict_id,memory_id,versions_json,created_at,versions_hash) "
                "VALUES(?,?,?,?,?)",
                (conflict_id, memory_id, versions_json, created_at, versions_hash),
            )
            db.execute(
                "INSERT INTO memory_conflict_audit(conflict_id,action,actor,event_at,detail_json) VALUES(?,?,?,?,?)",
                (conflict_id, "conflict_recorded", "system", created_at,
                 _canonical_json({"memory_id": memory_id, "versions_hash": versions_hash})),
            )
            row = db.execute("SELECT * FROM memory_conflicts WHERE conflict_id=?", (conflict_id,)).fetchone()
            return self._decode(row)

    def resolve(
        self,
        conflict_id: str,
        *,
        chosen_version_id: str,
        actor: str,
        resolved_at: str,
        note: str = "",
    ) -> ConflictRecord:
        conflict_id = _valid_id(conflict_id, "conflict_id")
        chosen_version_id = _valid_id(chosen_version_id, "chosen_version_id")
        actor = _valid_id(actor, "actor")
        if not isinstance(resolved_at, str) or not resolved_at.strip() or len(resolved_at) > 64:
            raise InvalidConflict("resolved_at must be a non-empty bounded timestamp string.")
        if not isinstance(note, str) or len(note) > 1000:
            raise InvalidConflict("note must be text no longer than 1000 characters.")
        with self._connect() as db:
            conflict = db.execute(
                "SELECT * FROM memory_conflicts WHERE conflict_id=?", (conflict_id,)
            ).fetchone()
            if conflict is None:
                raise ConflictLedgerError("Cannot resolve an unknown conflict.")
            versions = json.loads(conflict["versions_json"])
            if chosen_version_id not in {v["version_id"] for v in versions}:
                raise InvalidConflict("Chosen version must belong to the recorded conflict.")
            existing = db.execute(
                "SELECT * FROM memory_conflict_resolutions WHERE conflict_id=?", (conflict_id,)
            ).fetchone()
            if existing:
                if (existing["chosen_version_id"] == chosen_version_id and
                    existing["resolved_by"] == actor and existing["resolved_at"] == resolved_at and
                    existing["note"] == note):
                    return self._decode(conflict, existing)
                raise ConflictLedgerError("Conflict already resolved; resolution is immutable.")
            db.execute(
                "INSERT INTO memory_conflict_resolutions(conflict_id,chosen_version_id,resolved_by,resolved_at,note) "
                "VALUES(?,?,?,?,?)",
                (conflict_id, chosen_version_id, actor, resolved_at, note),
            )
            db.execute(
                "INSERT INTO memory_conflict_audit(conflict_id,action,actor,event_at,detail_json) VALUES(?,?,?,?,?)",
                (conflict_id, "conflict_resolved", actor, resolved_at,
                 _canonical_json({"chosen_version_id": chosen_version_id, "note": note})),
            )
            resolution = db.execute(
                "SELECT * FROM memory_conflict_resolutions WHERE conflict_id=?", (conflict_id,)
            ).fetchone()
            return self._decode(conflict, resolution)

    def get(self, conflict_id: str) -> ConflictRecord | None:
        conflict_id = _valid_id(conflict_id, "conflict_id")
        with self._connect() as db:
            row = db.execute("SELECT * FROM memory_conflicts WHERE conflict_id=?", (conflict_id,)).fetchone()
            if row is None:
                return None
            resolution = db.execute(
                "SELECT * FROM memory_conflict_resolutions WHERE conflict_id=?", (conflict_id,)
            ).fetchone()
            return self._decode(row, resolution)

    def list_open(self, *, limit: int = 100) -> list[ConflictRecord]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= _MAX_BATCH:
            raise ValueError(f"limit must be between 1 and {_MAX_BATCH}.")
        with self._connect() as db:
            rows = db.execute(
                "SELECT c.* FROM memory_conflicts c LEFT JOIN memory_conflict_resolutions r "
                "ON c.conflict_id=r.conflict_id WHERE r.conflict_id IS NULL "
                "ORDER BY c.created_at, c.conflict_id LIMIT ?", (limit,)
            ).fetchall()
            return [self._decode(row) for row in rows]

    def audit_events(self, conflict_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        conflict_id = _valid_id(conflict_id, "conflict_id")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= _MAX_BATCH:
            raise ValueError(f"limit must be between 1 and {_MAX_BATCH}.")
        with self._connect() as db:
            rows = db.execute(
                "SELECT audit_id, conflict_id, action, actor, event_at, detail_json "
                "FROM memory_conflict_audit WHERE conflict_id=? ORDER BY audit_id LIMIT ?",
                (conflict_id, limit),
            ).fetchall()
        return [{
            "audit_id": row["audit_id"], "conflict_id": row["conflict_id"],
            "action": row["action"], "actor": row["actor"], "event_at": row["event_at"],
            "detail": json.loads(row["detail_json"]),
        } for row in rows]
