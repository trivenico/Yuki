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
from typing import Any, Mapping


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
    approved_by: str | None
    approved_at: str | None
    approved_session_hash: str | None


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
                    lease_expires_at TEXT,
                    approved_by TEXT,
                    approved_at TEXT,
                    approval_digest TEXT,
                    approved_session_hash TEXT
                )
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(workflow_runs)")}
            for column in ("approved_by", "approved_at", "approval_digest", "approved_session_hash"):
                if column not in columns:
                    db.execute("ALTER TABLE workflow_runs ADD COLUMN " + column + " TEXT")
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_workflow_runs_state_updated "
                "ON workflow_runs(state, updated_at)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_workflow_runs_lease "
                "ON workflow_runs(state, lease_expires_at)"
            )
            db.execute("""
                CREATE TABLE IF NOT EXISTS task_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(task_id, sequence),
                    FOREIGN KEY(task_id) REFERENCES workflow_runs(run_id) ON DELETE CASCADE
                )
            """)
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_task_events_task_sequence "
                "ON task_events(task_id, sequence)"
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
            approved_by=row["approved_by"],
            approved_at=row["approved_at"],
            approved_session_hash=row["approved_session_hash"],
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

    @staticmethod
    def _requires_approval(definition: list[dict[str, Any]]) -> bool:
        return any(step.get("requires_approval", True) is True for step in definition)

    def approve(self, run_id: str, *, actor_id: str, session_id: str, expected_version: int) -> WorkflowRun:
        """Persist approval for this exact definition; caller must authenticate actor_id."""
        if not isinstance(actor_id, str) or not actor_id.strip() or len(actor_id) > 200 or chr(0) in actor_id:
            raise ValueError("actor_id must be non-empty bounded text.")
        if not isinstance(session_id, str) or not session_id.strip() or len(session_id) > 512 or chr(0) in session_id:
            raise ValueError("session_id must be non-empty bounded text.")
        session_hash = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("expected_version must be a non-negative integer.")
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                raise KeyError("workflow run not found.")
            if row["version"] != expected_version:
                raise WorkflowConflict("workflow run version changed; reload before approving.")
            if row["state"] != "queued":
                raise WorkflowTransitionError("only queued workflows can be approved.")
            updated = db.execute("""
                UPDATE workflow_runs SET approved_by=?, approved_at=?,
                    approval_digest=request_digest, approved_session_hash=?,
                    updated_at=?, version=version+1
                WHERE run_id=? AND version=? AND state='queued'
            """, (actor_id.strip(), now, session_hash, now, run_id, expected_version))
            if updated.rowcount != 1:
                raise WorkflowConflict("workflow run changed concurrently.")
            return self._row(db.execute(
                "SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)
            ).fetchone())

    def claim_next(
        self, *, worker_id: str, lease_seconds: int = 60,
        workflow_prefix_limits: Mapping[str, int] | None = None,
    ) -> WorkflowRun | None:
        """Atomically claim queued work while enforcing optional cross-process name budgets.

        Prefix budgets are checked inside the same BEGIN IMMEDIATE transaction as
        the claim. A limit therefore applies across all workers sharing this SQLite
        database, not merely within one Python worker pool.
        """
        if workflow_prefix_limits is None:
            workflow_prefix_limits = {}
        if not isinstance(workflow_prefix_limits, Mapping) or len(workflow_prefix_limits) > 32:
            raise ValueError("workflow_prefix_limits must be a bounded mapping.")
        normalized_limits = {}
        for prefix, limit in workflow_prefix_limits.items():
            if (
                not isinstance(prefix, str) or not prefix.startswith("agent-")
                or not prefix.endswith("-") or len(prefix) > 90 or "\\x00" in prefix
            ):
                raise ValueError("workflow prefixes must be bounded agent-name prefixes.")
            if type(limit) is not int or not 1 <= limit <= 32:
                raise ValueError("workflow prefix limits must be integers from 1 to 32.")
            normalized_limits[prefix] = limit
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
            rows = db.execute(
                "SELECT * FROM workflow_runs WHERE state='queued' "
                "ORDER BY created_at, run_id LIMIT 100"
            ).fetchall()
            row = None
            for candidate in rows:
                if self._requires_approval(json.loads(candidate["definition_json"])) and not (
                    candidate["approved_by"] and candidate["approved_at"]
                    and candidate["approved_session_hash"]
                    and candidate["approval_digest"] == candidate["request_digest"]
                ):
                    continue
                matching_prefix = next(
                    (prefix for prefix in sorted(normalized_limits, key=len, reverse=True)
                     if candidate["workflow_name"].startswith(prefix)),
                    None,
                )
                if matching_prefix is not None:
                    active = db.execute(
                        "SELECT COUNT(*) FROM workflow_runs WHERE state='running' "
                        "AND substr(workflow_name, 1, ?) = ?",
                        (len(matching_prefix), matching_prefix),
                    ).fetchone()[0]
                    if active >= normalized_limits[matching_prefix]:
                        continue
                row = candidate
                break
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
            if to_state == "running" and self._requires_approval(current.definition):
                if not (
                    row["approved_by"] and row["approved_at"]
                    and row["approved_session_hash"]
                    and row["approval_digest"] == row["request_digest"]
                ):
                    raise WorkflowTransitionError("workflow requires explicit approval before execution.")
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


    def append_task_event(
        self, task_id: str, *, kind: str, actor_id: str,
        payload: dict[str, Any], expected_version: int,
    ):
        """Atomically append a bounded task event and increment the task version."""
        from automation.task_runtime import TaskEvent, _EVENT_KINDS
        if kind not in _EVENT_KINDS:
            raise ValueError("Unknown task event kind.")
        if not isinstance(actor_id, str) or not actor_id.strip() or len(actor_id) > 200 or "\x00" in actor_id:
            raise ValueError("actor_id must be non-empty bounded text.")
        if not isinstance(payload, dict):
            raise ValueError("task event payload must be an object.")
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("expected_version must be a non-negative integer.")
        payload_json = _bounded_json(payload, 8 * 1024, "task event payload")
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM workflow_runs WHERE run_id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError("task not found.")
            if row["version"] != expected_version:
                raise WorkflowConflict("task version changed; reload before appending an event.")
            if row["state"] in _TERMINAL:
                expected_terminal_event = {
                    "succeeded": "task.completed",
                    "failed": "task.failed",
                    "cancelled": "task.cancelled",
                    "interrupted": "task.interrupted",
                }.get(row["state"])
                if kind != expected_terminal_event:
                    raise WorkflowTransitionError("cannot append events to a terminal task.")
                prior_terminal = db.execute(
                    "SELECT 1 FROM task_events WHERE task_id=? AND kind IN "
                    "('task.completed', 'task.failed', 'task.cancelled', 'task.interrupted') LIMIT 1",
                    (task_id,),
                ).fetchone()
                if prior_terminal:
                    raise WorkflowTransitionError("terminal event has already been recorded.")
            updated = db.execute(
                "UPDATE workflow_runs SET updated_at=?, version=version+1 WHERE run_id=? AND version=?",
                (now, task_id, expected_version),
            )
            if updated.rowcount != 1:
                raise WorkflowConflict("task changed concurrently.")
            sequence = db.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM task_events WHERE task_id=?",
                (task_id,),
            ).fetchone()[0]
            cursor = db.execute(
                "INSERT INTO task_events(task_id, sequence, kind, actor_id, payload_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (task_id, sequence, kind, actor_id.strip(), payload_json, now),
            )
            return TaskEvent(cursor.lastrowid, task_id, sequence, kind, actor_id.strip(),
                             json.loads(payload_json), now)

    def list_task_events(self, task_id: str, *, after_sequence: int = 0, limit: int = 100):
        from automation.task_runtime import TaskEvent
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("task_id must be non-empty text.")
        if type(after_sequence) is not int or after_sequence < 0:
            raise ValueError("after_sequence must be a non-negative integer.")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("limit must be an integer from 1 to 500.")
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM task_events WHERE task_id=? AND sequence>? ORDER BY sequence LIMIT ?",
                (task_id, after_sequence, limit),
            ).fetchall()
            return [TaskEvent(row["event_id"], row["task_id"], row["sequence"], row["kind"],
                              row["actor_id"], json.loads(row["payload_json"]), row["created_at"])
                    for row in rows]
