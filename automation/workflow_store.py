"""Durable state store for bounded workflow runs.

This module persists workflow definitions and lifecycle metadata only. It does
not schedule work, execute handlers, or automatically replay interrupted runs.
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


class WorkflowStoreError(RuntimeError):
    """Base error for workflow persistence operations."""


class WorkflowConflict(WorkflowStoreError):
    """Raised for idempotency-key payload mismatch or stale versions."""


class WorkflowTransitionError(WorkflowStoreError):
    """Raised when a workflow lifecycle transition is invalid."""


_STATES = frozenset({
    "queued", "running", "waiting_approval", "succeeded", "failed",
    "cancelled", "interrupted",
})
_TERMINAL = frozenset({"succeeded", "cancelled", "interrupted"})
_TRANSITIONS = {
    "queued": frozenset({"running", "cancelled"}),
    "running": frozenset({"waiting_approval", "succeeded", "failed", "cancelled", "interrupted"}),
    "waiting_approval": frozenset({"queued", "cancelled"}),
    "failed": frozenset({"queued", "cancelled"}),
    "succeeded": frozenset(),
    "cancelled": frozenset(),
    "interrupted": frozenset(),
}
_MAX_DEFINITION_BYTES = 32 * 1024
_MAX_RESULT_BYTES = 16 * 1024
_MAX_KEY_CHARS = 200
_MAX_NAME_CHARS = 100
_MAX_STEPS = 20


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bounded_json(value: Any, limit: int, field: str) -> str:
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be JSON-serializable.") from exc
    if len(encoded.encode("utf-8")) > limit:
        raise ValueError(f"{field} exceeds the {limit}-byte limit.")
    return encoded


@dataclass(frozen=True)
class WorkflowRun:
    run_id: str
    idempotency_key: str
    workflow_name: str
    definition: list[dict[str, Any]]
    state: str
    result: dict[str, Any] | list[Any] | None
    error_code: str | None
    created_at: str
    updated_at: str
    version: int
    attempts: int
    lease_owner: str | None
    lease_expires_at: str | None


class WorkflowRunStore:
    """SQLite lifecycle journal with idempotent creation and compare-and-swap updates."""

    def __init__(self, database_path: str):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path must be a non-empty path.")
        self.database_path = str(Path(database_path).expanduser())
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS workflow_runs (
                    run_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    request_digest TEXT NOT NULL,
                    workflow_name TEXT NOT NULL,
                    definition_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    result_json TEXT,
                    error_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 0,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    lease_owner TEXT,
                    lease_expires_at TEXT
                )
            """)
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_workflow_runs_state_updated "
                "ON workflow_runs(state, updated_at)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_workflow_runs_lease "
                "ON workflow_runs(state, lease_expires_at)"
            )

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=5000")
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA synchronous=FULL")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _validate_name(name: str) -> str:
        if not isinstance(name, str) or not name.strip() or len(name) > _MAX_NAME_CHARS or "\x00" in name:
            raise ValueError("workflow_name must be non-empty text of at most 100 characters.")
        return name.strip()

    @staticmethod
    def _validate_definition(definition: Any) -> list[dict[str, Any]]:
        if not isinstance(definition, (list, tuple)) or not 1 <= len(definition) <= _MAX_STEPS:
            raise ValueError("definition must contain 1-20 steps.")
        from automation.workflow_engine import WorkflowEngine
        validated = [WorkflowEngine._validate_step(i, step) for i, step in enumerate(definition)]
        names = [step[0] for step in validated]
        if len(set(names)) != len(names):
            raise ValueError("workflow step names must be unique.")
        canonical = [
            {"name": name, "action": action, "argument": argument, "requires_approval": approval}
            for name, action, argument, approval in validated
        ]
        _bounded_json(canonical, _MAX_DEFINITION_BYTES, "definition")
        return canonical

    @staticmethod
    def _row(row: sqlite3.Row) -> WorkflowRun:
        return WorkflowRun(
            run_id=row["run_id"],
            idempotency_key=row["idempotency_key"],
            workflow_name=row["workflow_name"],
            definition=json.loads(row["definition_json"]),
            state=row["state"],
            result=json.loads(row["result_json"]) if row["result_json"] is not None else None,
            error_code=row["error_code"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            version=row["version"],
            attempts=row["attempts"],
            lease_owner=row["lease_owner"],
            lease_expires_at=row["lease_expires_at"],
        )

    def create(self, *, idempotency_key: str, workflow_name: str, definition: list[dict[str, Any]]) -> WorkflowRun:
        if (
            not isinstance(idempotency_key, str)
            or not idempotency_key.strip()
            or len(idempotency_key) > _MAX_KEY_CHARS
            or "\x00" in idempotency_key
        ):
            raise ValueError("idempotency_key must be non-empty text of at most 200 characters.")
        key = idempotency_key.strip()
        name = self._validate_name(workflow_name)
        canonical = self._validate_definition(definition)
        definition_json = _bounded_json(canonical, _MAX_DEFINITION_BYTES, "definition")
        digest = hashlib.sha256(
            (name + "\n" + definition_json).encode("utf-8")
        ).hexdigest()
        now = _now()
        with self._connect() as db:
            # Serialize the read-before-insert path so concurrent retries with
            # the same key return the same run instead of leaking IntegrityError.
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT * FROM workflow_runs WHERE idempotency_key=?", (key,)
            ).fetchone()
            if existing:
                if existing["request_digest"] != digest:
                    raise WorkflowConflict("idempotency key was already used for a different workflow.")
                return self._row(existing)
            run_id = secrets.token_urlsafe(18)
            db.execute("""
                INSERT INTO workflow_runs (
                    run_id, idempotency_key, request_digest, workflow_name,
                    definition_json, state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'queued', ?, ?)
            """, (run_id, key, digest, name, definition_json, now, now))
            return self._row(db.execute(
                "SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)
            ).fetchone())

    def claim_next(self, *, worker_id: str, lease_seconds: int = 60) -> WorkflowRun | None:
        """Atomically claim the oldest queued run; never steals or replays running work."""
        if not isinstance(worker_id, str) or not worker_id.strip() or len(worker_id) > 200 or "\x00" in worker_id:
            raise ValueError("worker_id must be non-empty bounded text.")
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be an integer from 1 to 3600.")
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        lease_expires = (now_dt + timedelta(seconds=lease_seconds)).isoformat()
        with self._connect() as db:
            # Serialize selection and claim so concurrent workers cannot claim
            # the same queued run.
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM workflow_runs WHERE state='queued' "
                "ORDER BY created_at, run_id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            updated = db.execute("""
                UPDATE workflow_runs SET state='running', updated_at=?,
                    version=version+1, attempts=attempts+1, lease_owner=?,
                    lease_expires_at=?
                WHERE run_id=? AND version=? AND state='queued'
            """, (now, worker_id.strip(), lease_expires, row["run_id"], row["version"]))
            if updated.rowcount != 1:
                raise WorkflowConflict("workflow run was claimed concurrently.")
            claimed = db.execute(
                "SELECT * FROM workflow_runs WHERE run_id=?", (row["run_id"],)
            ).fetchone()
            return self._row(claimed)

    def renew_lease(
        self, run_id: str, *, worker_id: str, expected_version: int,
        lease_seconds: int = 60,
    ) -> WorkflowRun:
        """Renew only the active worker's unexpired lease using compare-and-swap."""
        if not isinstance(worker_id, str) or not worker_id.strip() or len(worker_id) > 200:
            raise ValueError("worker_id must be non-empty bounded text.")
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("expected_version must be a non-negative integer.")
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be an integer from 1 to 3600.")
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        lease_expires = (now_dt + timedelta(seconds=lease_seconds)).isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError("workflow run not found.")
            if row["state"] != "running" or row["lease_owner"] != worker_id:
                raise WorkflowTransitionError("only the current worker may renew a running lease.")
            if row["version"] != expected_version:
                raise WorkflowConflict("workflow run version changed; reload before renewing.")
            if not row["lease_expires_at"] or row["lease_expires_at"] < now:
                raise WorkflowConflict("expired workflow lease cannot be renewed.")
            updated = db.execute("""
                UPDATE workflow_runs SET lease_expires_at=?, updated_at=?, version=version+1
                WHERE run_id=? AND version=? AND state='running'
                    AND lease_owner=? AND lease_expires_at>=?
            """, (lease_expires, now, run_id, expected_version, worker_id, now))
            if updated.rowcount != 1:
                raise WorkflowConflict("workflow lease changed concurrently or expired.")
            renewed = db.execute(
                "SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            return self._row(renewed)

    def get(self, run_id: str) -> WorkflowRun | None:
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("run_id must be non-empty text.")
        with self._connect() as db:
            row = db.execute("SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)).fetchone()
            return self._row(row) if row else None

    def list_runs(self, *, state: str | None = None, limit: int = 50) -> list[WorkflowRun]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer from 1 to 100.")
        if state is not None and state not in _STATES:
            raise ValueError("Unknown workflow state.")
        with self._connect() as db:
            if state is None:
                rows = db.execute(
                    "SELECT * FROM workflow_runs ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM workflow_runs WHERE state=? ORDER BY created_at DESC LIMIT ?",
                    (state, limit),
                ).fetchall()
            return [self._row(row) for row in rows]

    def transition(
        self,
        run_id: str,
        *,
        expected_version: int,
        to_state: str,
        result: dict[str, Any] | list[Any] | None = None,
        error_code: str | None = None,
        lease_owner: str | None = None,
        lease_seconds: int = 60,
    ) -> WorkflowRun:
        if to_state not in _STATES:
            raise ValueError("Unknown workflow state.")
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("expected_version must be a non-negative integer.")
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be an integer from 1 to 3600.")
        if error_code is not None and (
            not isinstance(error_code, str) or not error_code or len(error_code) > 100
            or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for ch in error_code)
        ):
            raise ValueError("error_code must be a bounded machine-readable code.")
        result_json = None if result is None else _bounded_json(result, _MAX_RESULT_BYTES, "result")
        if to_state == "running":
            if not isinstance(lease_owner, str) or not lease_owner.strip() or len(lease_owner) > 200:
                raise ValueError("running state requires a lease_owner.")
        else:
            lease_owner = None

        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        lease_expires = (now_dt + timedelta(seconds=lease_seconds)).isoformat() if to_state == "running" else None
        with self._connect() as db:
            row = db.execute("SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                raise KeyError("workflow run not found.")
            current = self._row(row)
            if current.version != expected_version:
                raise WorkflowConflict("workflow run version changed; reload before updating.")
            if to_state not in _TRANSITIONS[current.state]:
                raise WorkflowTransitionError(f"transition {current.state!r} -> {to_state!r} is not allowed.")
            attempts = current.attempts + (1 if to_state == "running" else 0)
            updated = db.execute("""
                UPDATE workflow_runs SET state=?, result_json=?, error_code=?,
                    updated_at=?, version=version+1, attempts=?, lease_owner=?,
                    lease_expires_at=?
                WHERE run_id=? AND version=?
            """, (
                to_state, result_json, error_code, now, attempts, lease_owner,
                lease_expires, run_id, expected_version
            ))
            if updated.rowcount != 1:
                raise WorkflowConflict("workflow run changed concurrently.")
            return self._row(db.execute(
                "SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)
            ).fetchone())

    def mark_expired_running_as_interrupted(self, *, now: datetime | None = None, limit: int = 50) -> list[WorkflowRun]:
        """Mark expired leases interrupted; never automatically replay side effects."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer from 1 to 100.")
        current_time = now or datetime.now(timezone.utc)
        if current_time.tzinfo is None:
            raise ValueError("now must be timezone-aware.")
        cutoff = current_time.astimezone(timezone.utc).isoformat()
        changed: list[WorkflowRun] = []
        with self._connect() as db:
            rows = db.execute("""
                SELECT * FROM workflow_runs
                WHERE state='running' AND lease_expires_at IS NOT NULL
                    AND lease_expires_at < ?
                ORDER BY lease_expires_at LIMIT ?
            """, (cutoff, limit)).fetchall()
            for row in rows:
                updated = db.execute("""
                    UPDATE workflow_runs SET state='interrupted', error_code='lease_expired',
                        updated_at=?, version=version+1, lease_owner=NULL, lease_expires_at=NULL
                    WHERE run_id=? AND version=? AND state='running'
                """, (cutoff, row["run_id"], row["version"]))
                if updated.rowcount == 1:
                    changed_row = db.execute(
                        "SELECT * FROM workflow_runs WHERE run_id=?", (row["run_id"],)
                    ).fetchone()
                    changed.append(self._row(changed_row))
        return changed
