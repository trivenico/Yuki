"""Durable, session-fenced registry for automation workers.

The registry is operational metadata only: it does not grant task permissions,
claim work, or renew task leases. A fresh registration replaces any prior
session for the same worker ID, fencing late heartbeats from an old process.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
from typing import Any

from core.sqlite_file_security import prepare_private_sqlite_path


_MAX_WORKER_ID = 200
_WORKER_SESSION_STALE_AFTER_SECONDS = 60
_MAX_SESSION_TOKEN = 256
_MAX_METADATA_BYTES = 4 * 1024
_MAX_METADATA_FIELDS = 32


class WorkerRegistryError(RuntimeError):
    """Base error for durable worker registry operations."""


class WorkerSessionError(WorkerRegistryError):
    """The supplied process session is no longer the current worker session."""


@dataclass(frozen=True)
class WorkerSession:
    worker_id: str
    session_token: str
    registered_at: str


@dataclass(frozen=True)
class WorkerRecord:
    worker_id: str
    status: str
    started_at: str
    last_seen_at: str
    metadata: dict[str, Any]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.isoformat()


def _validate_worker_id(worker_id: str) -> str:
    if (
        not isinstance(worker_id, str)
        or not worker_id.strip()
        or len(worker_id) > _MAX_WORKER_ID
        or "\x00" in worker_id
    ):
        raise ValueError("worker_id must be non-empty bounded text.")
    return worker_id.strip()


def _session_digest(session_token: str) -> str:
    if (
        not isinstance(session_token, str)
        or not session_token
        or len(session_token) > _MAX_SESSION_TOKEN
        or "\x00" in session_token
    ):
        raise ValueError("session_token must be non-empty bounded text.")
    return hashlib.sha256(session_token.encode("utf-8")).hexdigest()


def _validate_metadata(metadata: Any) -> tuple[dict[str, Any], str]:
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict) or len(metadata) > _MAX_METADATA_FIELDS:
        raise ValueError("metadata must be an object with at most 32 fields.")
    if any(
        not isinstance(key, str) or not key or len(key) > 64 or "\x00" in key
        for key in metadata
    ):
        raise ValueError("metadata keys must be bounded non-empty text.")
    try:
        encoded = json.dumps(
            metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("metadata must contain finite JSON-compatible values.") from exc
    if len(encoded.encode("utf-8")) > _MAX_METADATA_BYTES:
        raise ValueError("metadata exceeds the 4096-byte limit.")
    return dict(metadata), encoded


class WorkerRegistry:
    """SQLite registry with process-session fencing and derived stale status."""

    def __init__(self, database_path: str | Path):
        self.database_path = prepare_private_sqlite_path(database_path)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS automation_workers (
                    worker_id TEXT PRIMARY KEY,
                    session_hash TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('active', 'stopping', 'stopped')),
                    started_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                )
            """)
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_automation_workers_status_seen "
                "ON automation_workers(status, last_seen_at)"
            )

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

    def register(
        self, worker_id: str, *, metadata: dict[str, Any] | None = None,
    ) -> WorkerSession:
        """Start a new process session, invalidating any older session for this ID."""
        worker_id = _validate_worker_id(worker_id)
        _, metadata_json = _validate_metadata(metadata)
        now = _timestamp(_utc_now())
        token = secrets.token_urlsafe(32)
        digest = _session_digest(token)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""
                INSERT INTO automation_workers (
                    worker_id, session_hash, status, started_at, last_seen_at, metadata_json
                ) VALUES (?, ?, 'active', ?, ?, ?)
                ON CONFLICT(worker_id) DO UPDATE SET
                    session_hash=excluded.session_hash,
                    status='active',
                    started_at=excluded.started_at,
                    last_seen_at=excluded.last_seen_at,
                    metadata_json=excluded.metadata_json
            """, (worker_id, digest, now, now, metadata_json))
        return WorkerSession(worker_id, token, now)

    def assert_current_session(self, worker_id: str, *, session_token: str) -> None:
        """Synchronously verify this session and its parent are current and fresh."""
        worker_id = _validate_worker_id(worker_id)
        digest = _session_digest(session_token)
        with self._connect() as db:
            row = db.execute(
                "SELECT metadata_json FROM automation_workers "
                "WHERE worker_id=? AND session_hash=? AND status='active'",
                (worker_id, digest),
            ).fetchone()
            if row is None:
                raise WorkerSessionError("worker session is not current or is not active.")
            try:
                metadata = json.loads(row["metadata_json"])
            except (TypeError, ValueError) as exc:
                raise WorkerSessionError("worker registry metadata is invalid.") from exc
            self._assert_parent_fresh(db, metadata)

    @staticmethod
    def _assert_parent_fresh(db: sqlite3.Connection, metadata: dict[str, Any]) -> None:
        if metadata.get("role") != "specialist_pool_child":
            return
        parent_id = metadata.get("parent_worker_id")
        parent_hash = metadata.get("parent_session_hash")
        if (
            not isinstance(parent_id, str) or not parent_id.strip()
            or not isinstance(parent_hash, str)
            or len(parent_hash) != 64
            or any(char not in "0123456789abcdef" for char in parent_hash)
        ):
            raise WorkerSessionError("child worker parent session binding is invalid.")
        cutoff = _timestamp(_utc_now() - timedelta(seconds=_WORKER_SESSION_STALE_AFTER_SECONDS))
        parent = db.execute(
            "SELECT 1 FROM automation_workers "
            "WHERE worker_id=? AND session_hash=? AND status='active' AND last_seen_at>=?",
            (parent_id, parent_hash, cutoff),
        ).fetchone()
        if parent is None:
            raise WorkerSessionError("worker parent session is stale or no longer active.")

    def heartbeat(self, worker_id: str, *, session_token: str) -> str:
        """Refresh a current active/stopping process session; never revive stopped sessions."""
        worker_id = _validate_worker_id(worker_id)
        digest = _session_digest(session_token)
        now = _timestamp(_utc_now())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT metadata_json FROM automation_workers "
                "WHERE worker_id=? AND session_hash=? AND status IN ('active', 'stopping')",
                (worker_id, digest),
            ).fetchone()
            if row is None:
                raise WorkerSessionError("worker session is not current or is stopped.")
            try:
                metadata = json.loads(row["metadata_json"])
            except (TypeError, ValueError) as exc:
                raise WorkerSessionError("worker registry metadata is invalid.") from exc
            self._assert_parent_fresh(db, metadata)
            updated = db.execute("""
                UPDATE automation_workers SET last_seen_at=?
                WHERE worker_id=? AND session_hash=? AND status IN ('active', 'stopping')
            """, (now, worker_id, digest))
            if updated.rowcount != 1:
                raise WorkerSessionError("worker session is not current or is stopped.")
        return now

    def heartbeat_child(
        self, worker_id: str, *, session_token: str,
        parent_worker_id: str, parent_session_token: str,
    ) -> str:
        """Refresh a child only while both its own and its parent session are current."""
        worker_id = _validate_worker_id(worker_id)
        parent_worker_id = _validate_worker_id(parent_worker_id)
        child_digest = _session_digest(session_token)
        parent_digest = _session_digest(parent_session_token)
        if worker_id == parent_worker_id:
            raise ValueError("child and parent worker IDs must differ.")
        now = _timestamp(_utc_now())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cutoff = _timestamp(_utc_now() - timedelta(seconds=_WORKER_SESSION_STALE_AFTER_SECONDS))
            parent = db.execute("""
                SELECT 1 FROM automation_workers
                WHERE worker_id=? AND session_hash=? AND status='active'
                    AND last_seen_at>=?
            """, (parent_worker_id, parent_digest, cutoff)).fetchone()
            if parent is None:
                raise WorkerSessionError("parent worker session is stale or no longer active.")
            child_row = db.execute("""
                SELECT metadata_json FROM automation_workers
                WHERE worker_id=? AND session_hash=? AND status='active'
            """, (worker_id, child_digest)).fetchone()
            if child_row is None:
                raise WorkerSessionError("child worker session is not current or active.")
            try:
                child_metadata = json.loads(child_row["metadata_json"])
            except (TypeError, ValueError) as exc:
                raise WorkerSessionError("child worker registry metadata is invalid.") from exc
            if (
                child_metadata.get("role") != "specialist_pool_child"
                or child_metadata.get("parent_worker_id") != parent_worker_id
                or child_metadata.get("parent_session_hash") != parent_digest
            ):
                raise WorkerSessionError("child worker is not bound to this parent session.")
            child = db.execute("""
                UPDATE automation_workers SET last_seen_at=?
                WHERE worker_id=? AND session_hash=? AND status='active'
            """, (now, worker_id, child_digest))
            if child.rowcount != 1:
                raise WorkerSessionError("child worker session is not current or active.")
        return now

    def set_status(
        self, worker_id: str, *, session_token: str, status: str,
    ) -> WorkerRecord:
        """Move the current session to stopping/stopped without changing its identity."""
        worker_id = _validate_worker_id(worker_id)
        digest = _session_digest(session_token)
        if not isinstance(status, str) or status not in {"stopping", "stopped"}:
            raise ValueError("status must be 'stopping' or 'stopped'.")
        now = _timestamp(_utc_now())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            updated = db.execute("""
                UPDATE automation_workers SET status=?, last_seen_at=?
                WHERE worker_id=? AND session_hash=? AND status IN ('active', 'stopping')
            """, (status, now, worker_id, digest))
            if updated.rowcount != 1:
                raise WorkerSessionError("worker session is not current or is stopped.")
            row = db.execute(
                "SELECT * FROM automation_workers WHERE worker_id=?", (worker_id,)
            ).fetchone()
            return self._record(row, stale_after_seconds=None)

    @staticmethod
    def _record(row: sqlite3.Row, *, stale_after_seconds: int | None) -> WorkerRecord:
        status = row["status"]
        if stale_after_seconds is not None and status in {"active", "stopping"}:
            cutoff = _timestamp(_utc_now() - timedelta(seconds=stale_after_seconds))
            if row["last_seen_at"] < cutoff:
                status = "stale"
        return WorkerRecord(
            worker_id=row["worker_id"],
            status=status,
            started_at=row["started_at"],
            last_seen_at=row["last_seen_at"],
            metadata=json.loads(row["metadata_json"]),
        )

    def list_workers(
        self, *, stale_after_seconds: int = 60, limit: int = 100,
    ) -> list[WorkerRecord]:
        """List bounded operational records; stale is derived and never mutates storage."""
        if type(stale_after_seconds) is not int or not 1 <= stale_after_seconds <= 86_400:
            raise ValueError("stale_after_seconds must be an integer from 1 to 86400.")
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be an integer from 1 to 1000.")
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM automation_workers ORDER BY started_at DESC, worker_id LIMIT ?",
                (limit,),
            ).fetchall()
            return [
                self._record(row, stale_after_seconds=stale_after_seconds)
                for row in rows
            ]
