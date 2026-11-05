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
class WorkflowStepRecord:
    step_id: str
    run_id: str
    step_index: int
    name: str
    action: str
    requires_approval: bool
    input_digest: str
    status: str
    attempts: int
    version: int
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class WorkflowOperation:
    operation_id: str
    task_id: str
    idempotency_key: str
    action: str
    request_digest: str
    status: str
    provider_reference: str | None
    outcome: dict[str, Any] | list[Any] | None
    version: int
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class OperationAuditEvent:
    event_id: int
    task_id: str
    operation_id: str
    sequence: int
    from_status: str | None
    to_status: str
    actor_type: str
    actor_id: str
    principal_id: str | None
    reason: str
    created_at: str


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
            db.execute("""
                CREATE TABLE IF NOT EXISTS workflow_steps (
                    step_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    step_index INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    action TEXT NOT NULL,
                    requires_approval INTEGER NOT NULL CHECK(requires_approval IN (0, 1)),
                    input_digest TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending', 'running', 'succeeded', 'failed', 'needs_reconciliation')),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    version INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(run_id, step_index),
                    FOREIGN KEY(run_id) REFERENCES workflow_runs(run_id) ON DELETE CASCADE
                )
            """)
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_workflow_steps_run_order "
                "ON workflow_steps(run_id, step_index)"
            )
            db.execute("""
                CREATE TABLE IF NOT EXISTS workflow_operations (
                    operation_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    action TEXT NOT NULL,
                    request_digest TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN
                        ('prepared', 'in_flight', 'succeeded', 'needs_reconciliation')),
                    provider_reference TEXT,
                    outcome_json TEXT,
                    version INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(task_id, idempotency_key),
                    FOREIGN KEY(task_id) REFERENCES workflow_runs(run_id) ON DELETE CASCADE
                )
            """)
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_workflow_operations_task_status "
                "ON workflow_operations(task_id, status)"
            )
            db.execute("""
                CREATE TABLE IF NOT EXISTS operation_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    operation_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    from_status TEXT,
                    to_status TEXT NOT NULL CHECK(to_status IN
                        ('prepared', 'in_flight', 'succeeded', 'needs_reconciliation')),
                    actor_type TEXT NOT NULL CHECK(actor_type IN ('system', 'worker')),
                    actor_id TEXT NOT NULL,
                    principal_id TEXT,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(operation_id, sequence),
                    FOREIGN KEY(task_id) REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
                    FOREIGN KEY(operation_id) REFERENCES workflow_operations(operation_id) ON DELETE RESTRICT
                )
            """)
            event_columns = {row["name"] for row in db.execute("PRAGMA table_info(operation_events)")}
            if "principal_id" not in event_columns:
                db.execute("ALTER TABLE operation_events ADD COLUMN principal_id TEXT")
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_operation_events_task_created "
                "ON operation_events(task_id, event_id)"
            )
            db.execute("""
                CREATE TRIGGER IF NOT EXISTS operation_events_no_update
                BEFORE UPDATE ON operation_events
                BEGIN SELECT RAISE(ABORT, 'operation audit events are immutable'); END
            """)
            db.execute("""
                CREATE TRIGGER IF NOT EXISTS operation_events_no_delete
                BEFORE DELETE ON operation_events
                BEGIN SELECT RAISE(ABORT, 'operation audit events are immutable'); END
            """)

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

    def create(
        self, *, idempotency_key: str, workflow_name: str, definition: list[dict[str, Any]],
        event_actor_id: str | None = None, event_payload: dict[str, Any] | None = None,
    ) -> WorkflowRun:
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
        event_json = None
        if event_actor_id is not None:
            if (not isinstance(event_actor_id, str) or not event_actor_id.strip()
                    or len(event_actor_id) > 200 or chr(0) in event_actor_id):
                raise ValueError("event_actor_id must be non-empty bounded text.")
            if event_payload is None:
                event_payload = {"name": name, "step_count": len(canonical)}
            if not isinstance(event_payload, dict):
                raise ValueError("event_payload must be an object.")
            event_json = _bounded_json(event_payload, 8 * 1024, "task event payload")
        elif event_payload is not None:
            raise ValueError("event_payload requires event_actor_id.")
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
            for step_index, step in enumerate(canonical):
                argument_json = _bounded_json(step.get("argument"), _MAX_DEFINITION_BYTES, "step argument")
                input_digest = hashlib.sha256(argument_json.encode("utf-8")).hexdigest()
                db.execute("""
                    INSERT INTO workflow_steps (
                        step_id, run_id, step_index, name, action, requires_approval,
                        input_digest, status, attempts, version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', 0, 0, ?, ?)
                """, (
                    f"{run_id}:{step_index}", run_id, step_index, step["name"],
                    step["action"], int(step["requires_approval"]), input_digest, now, now,
                ))
            if event_json is not None:
                db.execute(
                    "INSERT INTO task_events(task_id, sequence, kind, actor_id, payload_json, created_at) "
                    "VALUES (?, 1, 'task.created', ?, ?, ?)",
                    (run_id, event_actor_id.strip(), event_json, now),
                )
            return self._row(db.execute(
                "SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)
            ).fetchone())

    def transition_step(
        self, run_id: str, step_id: str, *, worker_id: str, actor_id: str,
        expected_step_version: int, to_status: str,
    ) -> WorkflowStepRecord:
        """CAS a step transition while holding an unexpired parent worker lease.

        Step state and its task journal event are committed in one transaction.
        This method never executes a step or retries external side effects.
        """
        transitions = {
            "pending": frozenset({"running"}),
            "running": frozenset({"succeeded", "failed", "needs_reconciliation"}),
            "succeeded": frozenset(),
            "failed": frozenset(),
            "needs_reconciliation": frozenset(),
        }
        event_kinds = {
            "running": "task.step.started",
            "succeeded": "task.step.succeeded",
            "failed": "task.step.failed",
            "needs_reconciliation": "task.step.needs_reconciliation",
        }
        for value, label in ((run_id, "run_id"), (step_id, "step_id"),
                             (worker_id, "worker_id"), (actor_id, "actor_id")):
            if not isinstance(value, str) or not value.strip() or len(value) > 200 or "\x00" in value:
                raise ValueError(f"{label} must be non-empty bounded text.")
        if type(expected_step_version) is not int or expected_step_version < 0:
            raise ValueError("expected_step_version must be a non-negative integer.")
        if not isinstance(to_status, str) or to_status not in event_kinds:
            raise ValueError("Unknown workflow step status.")
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            parent = db.execute(
                "SELECT state, lease_owner, lease_expires_at FROM workflow_runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if parent is None:
                raise KeyError("workflow run not found.")
            if parent["state"] != "running" or parent["lease_owner"] != worker_id:
                raise WorkflowConflict("step update requires the current worker's unexpired task lease.")
            expiry = parent["lease_expires_at"]
            if not expiry:
                raise WorkflowConflict("step update requires the current worker's unexpired task lease.")
            try:
                expiry_dt = datetime.fromisoformat(expiry)
            except (TypeError, ValueError) as exc:
                raise WorkflowConflict(
                    "workflow worker lease expiry is malformed; manual inspection is required."
                ) from exc
            if expiry_dt.tzinfo is None or expiry_dt.utcoffset() is None:
                raise WorkflowConflict(
                    "workflow worker lease expiry must include a timezone; manual inspection is required."
                )
            if expiry_dt <= now_dt:
                raise WorkflowConflict("step update requires the current worker's unexpired task lease.")
            row = db.execute(
                "SELECT * FROM workflow_steps WHERE step_id=? AND run_id=?",
                (step_id, run_id),
            ).fetchone()
            if row is None:
                raise KeyError("workflow step not found.")
            if row["version"] != expected_step_version:
                raise WorkflowConflict("workflow step version changed; reload before updating.")
            if to_status not in transitions[row["status"]]:
                raise WorkflowTransitionError(
                    f"step transition {row['status']!r} -> {to_status!r} is not allowed."
                )
            attempts = row["attempts"] + (1 if to_status == "running" else 0)
            updated = db.execute("""
                UPDATE workflow_steps SET status=?, attempts=?, version=version+1, updated_at=?
                WHERE step_id=? AND run_id=? AND version=?
            """, (to_status, attempts, now, step_id, run_id, expected_step_version))
            if updated.rowcount != 1:
                raise WorkflowConflict("workflow step changed concurrently.")
            sequence = db.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM task_events WHERE task_id=?",
                (run_id,),
            ).fetchone()[0]
            payload = _bounded_json({
                "step_id": step_id, "step_index": row["step_index"],
                "step_name": row["name"], "status": to_status,
                "step_version": expected_step_version + 1,
                "worker_id": worker_id,
            }, 8 * 1024, "task event payload")
            db.execute(
                "INSERT INTO task_events(task_id, sequence, kind, actor_id, payload_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, sequence, event_kinds[to_status], actor_id.strip(), payload, now),
            )
            updated_row = db.execute(
                "SELECT * FROM workflow_steps WHERE step_id=?", (step_id,)
            ).fetchone()
            return WorkflowStepRecord(
                step_id=updated_row["step_id"], run_id=updated_row["run_id"],
                step_index=updated_row["step_index"], name=updated_row["name"],
                action=updated_row["action"],
                requires_approval=bool(updated_row["requires_approval"]),
                input_digest=updated_row["input_digest"], status=updated_row["status"],
                attempts=updated_row["attempts"], version=updated_row["version"],
                created_at=updated_row["created_at"], updated_at=updated_row["updated_at"],
            )

    def recover_expired_run(self, run_id: str, *, actor_id: str) -> WorkflowRun:
        """Interrupt an expired run and quarantine in-flight steps atomically.

        This is an operator recovery action, not a retry. No handler is invoked
        and no side effect is replayed.
        """
        if not isinstance(run_id, str) or not run_id.strip() or len(run_id) > 200 or "\x00" in run_id:
            raise ValueError("run_id must be non-empty bounded text.")
        if not isinstance(actor_id, str) or not actor_id.strip() or len(actor_id) > 200 or "\x00" in actor_id:
            raise ValueError("actor_id must be non-empty bounded text.")
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            parent = db.execute("SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)).fetchone()
            if parent is None:
                raise KeyError("workflow run not found.")
            if parent["state"] != "running":
                raise WorkflowTransitionError("only a running workflow can be recovered.")
            expiry = parent["lease_expires_at"]
            if not expiry:
                raise WorkflowConflict("workflow worker lease expiry is missing.")
            try:
                expiry_dt = datetime.fromisoformat(expiry)
            except (TypeError, ValueError) as exc:
                raise WorkflowConflict(
                    "workflow worker lease expiry is malformed; manual inspection is required."
                ) from exc
            if expiry_dt.tzinfo is None or expiry_dt.utcoffset() is None:
                raise WorkflowConflict(
                    "workflow worker lease expiry must include a timezone; manual inspection is required."
                )
            if expiry_dt >= now_dt:
                raise WorkflowConflict("workflow worker lease has not expired.")
            steps = db.execute(
                "SELECT * FROM workflow_steps WHERE run_id=? AND status='running' ORDER BY step_index",
                (run_id,),
            ).fetchall()
            sequence = db.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM task_events WHERE task_id=?",
                (run_id,),
            ).fetchone()[0]
            for step in steps:
                changed = db.execute("""
                    UPDATE workflow_steps SET status='needs_reconciliation',
                        version=version+1, updated_at=?
                    WHERE step_id=? AND version=? AND status='running'
                """, (now, step["step_id"], step["version"]))
                if changed.rowcount != 1:
                    raise WorkflowConflict("workflow step changed during recovery.")
                payload = _bounded_json({
                    "step_id": step["step_id"], "step_index": step["step_index"],
                    "step_name": step["name"], "status": "needs_reconciliation",
                    "step_version": step["version"] + 1,
                    "reason": "worker_lease_expired",
                }, 8 * 1024, "task event payload")
                db.execute(
                    "INSERT INTO task_events(task_id, sequence, kind, actor_id, payload_json, created_at) "
                    "VALUES (?, ?, 'task.step.needs_reconciliation', ?, ?, ?)",
                    (run_id, sequence, actor_id.strip(), payload, now),
                )
                sequence += 1
            changed = db.execute("""
                UPDATE workflow_runs SET state='interrupted', updated_at=?,
                    version=version+1, lease_owner=NULL, lease_expires_at=NULL
                WHERE run_id=? AND version=? AND state='running'
            """, (now, run_id, parent["version"]))
            if changed.rowcount != 1:
                raise WorkflowConflict("workflow run changed during recovery.")
            event_payload = _bounded_json({
                "reason": "worker_lease_expired",
                "automatic_replay": False,
                "reconciliation_required_steps": [step["step_id"] for step in steps],
            }, 8 * 1024, "task event payload")
            db.execute(
                "INSERT INTO task_events(task_id, sequence, kind, actor_id, payload_json, created_at) "
                "VALUES (?, ?, 'task.interrupted', ?, ?, ?)",
                (run_id, sequence, actor_id.strip(), event_payload, now),
            )
            return self._row(db.execute(
                "SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)
            ).fetchone())

    def reconcile_step(
        self, run_id: str, step_id: str, *, actor_id: str,
        expected_step_version: int, decision: str, reason: str,
    ) -> WorkflowStepRecord:
        """Record a supervisor's disposition without resuming or executing work."""
        for value, label in ((run_id, "run_id"), (step_id, "step_id"),
                             (actor_id, "actor_id")):
            if not isinstance(value, str) or not value.strip() or len(value) > 200 or "\x00" in value:
                raise ValueError(f"{label} must be non-empty bounded text.")
        if type(expected_step_version) is not int or expected_step_version < 0:
            raise ValueError("expected_step_version must be a non-negative integer.")
        if not isinstance(decision, str) or decision not in {"succeeded", "failed"}:
            raise ValueError("decision must be 'succeeded' or 'failed'.")
        if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 1000 or "\x00" in reason:
            raise ValueError("reason must be non-empty text of at most 1000 characters.")
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            parent = db.execute(
                "SELECT state FROM workflow_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if parent is None:
                raise KeyError("workflow run not found.")
            if parent["state"] != "interrupted":
                raise WorkflowTransitionError("only interrupted workflows can reconcile steps.")
            row = db.execute(
                "SELECT * FROM workflow_steps WHERE step_id=? AND run_id=?",
                (step_id, run_id),
            ).fetchone()
            if row is None:
                raise KeyError("workflow step not found.")
            if row["version"] != expected_step_version:
                raise WorkflowConflict("workflow step version changed; reload before reconciling.")
            if row["status"] != "needs_reconciliation":
                raise WorkflowTransitionError("only steps awaiting reconciliation can be resolved.")
            updated = db.execute("""
                UPDATE workflow_steps SET status=?, version=version+1, updated_at=?
                WHERE step_id=? AND run_id=? AND version=? AND status='needs_reconciliation'
            """, (decision, now, step_id, run_id, expected_step_version))
            if updated.rowcount != 1:
                raise WorkflowConflict("workflow step changed concurrently.")
            sequence = db.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM task_events WHERE task_id=?",
                (run_id,),
            ).fetchone()[0]
            payload = _bounded_json({
                "step_id": step_id, "step_index": row["step_index"],
                "step_name": row["name"], "decision": decision,
                "step_version": expected_step_version + 1,
                "reason": reason.strip(), "workflow_resumed": False,
            }, 8 * 1024, "task event payload")
            db.execute(
                "INSERT INTO task_events(task_id, sequence, kind, actor_id, payload_json, created_at) "
                "VALUES (?, ?, 'task.step.reconciled', ?, ?, ?)",
                (run_id, sequence, actor_id.strip(), payload, now),
            )
            updated_row = db.execute(
                "SELECT * FROM workflow_steps WHERE step_id=?", (step_id,)
            ).fetchone()
            return WorkflowStepRecord(
                step_id=updated_row["step_id"], run_id=updated_row["run_id"],
                step_index=updated_row["step_index"], name=updated_row["name"],
                action=updated_row["action"],
                requires_approval=bool(updated_row["requires_approval"]),
                input_digest=updated_row["input_digest"], status=updated_row["status"],
                attempts=updated_row["attempts"], version=updated_row["version"],
                created_at=updated_row["created_at"], updated_at=updated_row["updated_at"],
            )

    def list_steps(self, run_id: str) -> list[WorkflowStepRecord]:
        """Return ordered step metadata without exposing stored arguments or outputs."""
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("run_id must be non-empty text.")
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM workflow_steps WHERE run_id=? ORDER BY step_index",
                (run_id,),
            ).fetchall()
            return [
                WorkflowStepRecord(
                    step_id=row["step_id"], run_id=row["run_id"],
                    step_index=row["step_index"], name=row["name"], action=row["action"],
                    requires_approval=bool(row["requires_approval"]),
                    input_digest=row["input_digest"], status=row["status"],
                    attempts=row["attempts"], version=row["version"],
                    created_at=row["created_at"], updated_at=row["updated_at"],
                )
                for row in rows
            ]

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
            rows = db.execute(
                "SELECT * FROM workflow_runs WHERE state='queued' "
                "ORDER BY created_at, run_id LIMIT 100"
            ).fetchall()
            row = next((
                candidate for candidate in rows
                if not self._requires_approval(json.loads(candidate["definition_json"]))
                or (
                    candidate["approved_by"] and candidate["approved_at"]
                    and candidate["approved_session_hash"]
                    and candidate["approval_digest"] == candidate["request_digest"]
                )
            ), None)
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
            sequence = db.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM task_events WHERE task_id=?",
                (row["run_id"],),
            ).fetchone()[0]
            claim_payload = _bounded_json(
                {"attempt": row["attempts"] + 1, "lease_expires_at": lease_expires},
                8 * 1024, "task event payload",
            )
            db.execute(
                "INSERT INTO task_events(task_id, sequence, kind, actor_id, payload_json, created_at) "
                "VALUES (?, ?, 'task.claimed', ?, ?, ?)",
                (row["run_id"], sequence, worker_id.strip(), claim_payload, now),
            )
            claimed = db.execute(
                "SELECT * FROM workflow_runs WHERE run_id=?", (row["run_id"],)
            ).fetchone()
            return self._row(claimed)

    def renew_lease(
        self, run_id: str, *, worker_id: str, expected_version: int,
        lease_seconds: int = 60, event_actor_id: str | None = None,
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
            expiry = row["lease_expires_at"]
            if not expiry:
                raise WorkflowConflict("workflow lease expiry is missing.")
            try:
                expiry_dt = datetime.fromisoformat(expiry)
            except (TypeError, ValueError) as exc:
                raise WorkflowConflict(
                    "workflow lease expiry is malformed; renewal is unsafe."
                ) from exc
            if expiry_dt.tzinfo is None or expiry_dt.utcoffset() is None:
                raise WorkflowConflict(
                    "workflow lease expiry must include a timezone; renewal is unsafe."
                )
            if expiry_dt <= now_dt:
                raise WorkflowConflict("expired workflow lease cannot be renewed.")
            updated = db.execute("""
                UPDATE workflow_runs SET lease_expires_at=?, updated_at=?, version=version+1
                WHERE run_id=? AND version=? AND state='running'
                    AND lease_owner=? AND lease_expires_at>=?
            """, (lease_expires, now, run_id, expected_version, worker_id, now))
            if updated.rowcount != 1:
                raise WorkflowConflict("workflow lease changed concurrently or expired.")
            if event_actor_id is not None:
                if (not isinstance(event_actor_id, str) or not event_actor_id.strip()
                        or len(event_actor_id) > 200 or "\x00" in event_actor_id):
                    raise ValueError("event_actor_id must be non-empty bounded text.")
                sequence = db.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 FROM task_events WHERE task_id=?",
                    (run_id,),
                ).fetchone()[0]
                payload_json = _bounded_json(
                    {"worker_id": worker_id.strip(), "lease_expires_at": lease_expires},
                    8 * 1024, "task event payload",
                )
                db.execute(
                    "INSERT INTO task_events(task_id, sequence, kind, actor_id, payload_json, created_at) "
                    "VALUES (?, ?, 'task.lease_renewed', ?, ?, ?)",
                    (run_id, sequence, event_actor_id.strip(), payload_json, now),
                )
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

    def list_expired_runs(self, *, now: str, limit: int = 50) -> list[WorkflowRun]:
        """Return the oldest valid expired leases first for starvation-free recovery."""
        if not isinstance(now, str) or not now.strip():
            raise ValueError("now must be a non-empty ISO timestamp.")
        try:
            now_dt = datetime.fromisoformat(now)
        except ValueError as exc:
            raise ValueError("now must be a valid ISO timestamp.") from exc
        if now_dt.tzinfo is None or now_dt.utcoffset() is None:
            raise ValueError("now must include a timezone.")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer from 1 to 100.")
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM workflow_runs WHERE state='running' "
                "AND lease_expires_at IS NOT NULL "
                "AND lease_expires_at < ? "
                "AND (substr(lease_expires_at, 20, 1) IN ('+', '-') "
                "OR substr(lease_expires_at, -1) = 'Z') "
                "AND julianday(lease_expires_at) IS NOT NULL "
                "AND julianday(lease_expires_at) < julianday(?) "
                "ORDER BY julianday(lease_expires_at), created_at, run_id LIMIT ?",
                (now, now, limit),
            ).fetchall()
            return [self._row(row) for row in rows]

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
        expected_lease_owner: str | None = None,
        event_kind: str | None = None,
        event_actor_id: str | None = None,
        event_payload: dict[str, Any] | None = None,
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
        event_json = None
        if event_kind is not None:
            from automation.task_runtime import _EVENT_KINDS
            expected_kind = {"succeeded": "task.completed", "failed": "task.failed",
                             "cancelled": "task.cancelled", "interrupted": "task.interrupted"}.get(to_state)
            if event_kind not in _EVENT_KINDS or event_kind != expected_kind:
                raise ValueError("event_kind must match the terminal workflow state.")
            if not isinstance(event_actor_id, str) or not event_actor_id.strip() or len(event_actor_id) > 200 or "\x00" in event_actor_id:
                raise ValueError("event_actor_id must be non-empty bounded text.")
            if not isinstance(event_payload, dict):
                raise ValueError("event_payload must be an object.")
            event_json = _bounded_json(event_payload, 8 * 1024, "task event payload")
        elif event_actor_id is not None or event_payload is not None:
            raise ValueError("event actor and payload require event_kind.")
        if to_state == "running":
            if not isinstance(lease_owner, str) or not lease_owner.strip() or len(lease_owner) > 200:
                raise ValueError("running state requires a lease_owner.")
        else:
            lease_owner = None

        if expected_lease_owner is not None and (
            not isinstance(expected_lease_owner, str) or not expected_lease_owner.strip()
            or len(expected_lease_owner) > 200 or "\x00" in expected_lease_owner
            or to_state not in {"succeeded", "failed"}
        ):
            raise ValueError("expected_lease_owner is valid only for worker completion or failure.")
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        lease_expires = (now_dt + timedelta(seconds=lease_seconds)).isoformat() if to_state == "running" else None
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
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
            if to_state == "cancelled":
                active_operation = db.execute(
                    "SELECT 1 FROM workflow_operations WHERE task_id=? AND status='in_flight' LIMIT 1",
                    (run_id,),
                ).fetchone()
                if active_operation is not None:
                    raise WorkflowTransitionError(
                        "task cancellation is blocked while an external operation is in flight; "
                        "resolve or reconcile the operation first."
                    )
            if current.state == "running" and to_state in {"succeeded", "failed"} and expected_lease_owner is None:
                raise WorkflowConflict("worker completion and failure require the active worker identity.")
            if expected_lease_owner is not None:
                expiry = current.lease_expires_at
                if current.state != "running" or current.lease_owner != expected_lease_owner or not expiry:
                    raise WorkflowConflict("only the current worker with an unexpired lease may finish this task.")
                try:
                    expiry_dt = datetime.fromisoformat(expiry)
                except (TypeError, ValueError) as exc:
                    raise WorkflowConflict(
                        "workflow lease expiry is malformed; worker completion is unsafe."
                    ) from exc
                if expiry_dt.tzinfo is None or expiry_dt.utcoffset() is None:
                    raise WorkflowConflict(
                        "workflow lease expiry must include a timezone; worker completion is unsafe."
                    )
                if expiry_dt <= now_dt:
                    raise WorkflowConflict("only the current worker with an unexpired lease may finish this task.")
            attempts = current.attempts + (1 if to_state == "running" else 0)
            updated = db.execute("""
                UPDATE workflow_runs SET state=?, result_json=?, error_code=?,
                    updated_at=?, version=version+1, attempts=?, lease_owner=?,
                    lease_expires_at=?
                WHERE run_id=? AND version=? AND (?=0 OR (
                    state='running' AND lease_owner=? AND julianday(lease_expires_at)>=julianday(?)
                ))
            """, (
                to_state, result_json, error_code, now, attempts, lease_owner,
                lease_expires, run_id, expected_version, int(expected_lease_owner is not None),
                expected_lease_owner, now
            ))
            if updated.rowcount != 1:
                raise WorkflowConflict("workflow run changed concurrently.")
            if event_kind is not None:
                sequence = db.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 FROM task_events WHERE task_id=?",
                    (run_id,),
                ).fetchone()[0]
                db.execute(
                    "INSERT INTO task_events(task_id, sequence, kind, actor_id, payload_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (run_id, sequence, event_kind, event_actor_id.strip(), event_json, now),
                )
            return self._row(db.execute(
                "SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)
            ).fetchone())

    def mark_expired_running_as_interrupted(
        self, *, now: datetime | None = None, limit: int = 50,
        event_actor_id: str | None = None,
    ) -> list[WorkflowRun]:
        """Mark expired leases interrupted; never automatically replay side effects."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer from 1 to 100.")
        current_time = now or datetime.now(timezone.utc)
        if current_time.tzinfo is None:
            raise ValueError("now must be timezone-aware.")
        cutoff = current_time.astimezone(timezone.utc).isoformat()
        actor = None
        if event_actor_id is not None:
            if (not isinstance(event_actor_id, str) or not event_actor_id.strip()
                    or len(event_actor_id) > 200 or chr(0) in event_actor_id):
                raise ValueError("event_actor_id must be non-empty bounded text.")
            actor = event_actor_id.strip()
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
                    if actor is not None:
                        sequence = db.execute(
                            "SELECT COALESCE(MAX(sequence), 0) + 1 FROM task_events WHERE task_id=?",
                            (row["run_id"],),
                        ).fetchone()[0]
                        payload_json = _bounded_json(
                            {"state": "interrupted", "error_code": "lease_expired",
                             "automatic_replay": False}, 8 * 1024, "task event payload"
                        )
                        db.execute(
                            "INSERT INTO task_events(task_id, sequence, kind, actor_id, payload_json, created_at) "
                            "VALUES (?, ?, 'task.interrupted', ?, ?, ?)",
                            (row["run_id"], sequence, actor, payload_json, cutoff),
                        )
                    changed.append(self._row(changed_row))
        return changed


    @staticmethod
    def _operation_row(row):
        if row is None:
            return None
        return WorkflowOperation(
            operation_id=row["operation_id"], task_id=row["task_id"],
            idempotency_key=row["idempotency_key"], action=row["action"],
            request_digest=row["request_digest"], status=row["status"],
            provider_reference=row["provider_reference"],
            outcome=json.loads(row["outcome_json"]) if row["outcome_json"] else None,
            version=row["version"], created_at=row["created_at"], updated_at=row["updated_at"],
        )

    @staticmethod
    def _append_operation_event(db: sqlite3.Connection, *, operation: sqlite3.Row,
                                from_status: str | None, to_status: str,
                                actor_type: str, actor_id: str, reason: str,
                                created_at: str, principal_id: str | None = None) -> None:
        if actor_type not in {"system", "worker"}:
            raise ValueError("invalid operation audit actor type.")
        if not isinstance(actor_id, str) or not actor_id.strip() or len(actor_id) > 200 or "\\x00" in actor_id:
            raise ValueError("operation audit actor_id must be non-empty bounded text.")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 120 or "\\x00" in reason:
            raise ValueError("operation audit reason must be non-empty bounded text.")
        sequence = db.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 FROM operation_events WHERE operation_id=?",
            (operation["operation_id"],),
        ).fetchone()[0]
        db.execute(
            "INSERT INTO operation_events(task_id, operation_id, sequence, from_status, "
            "to_status, actor_type, actor_id, principal_id, reason, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (operation["task_id"], operation["operation_id"], sequence, from_status,
             to_status, actor_type, actor_id.strip(), principal_id, reason.strip(), created_at),
        )

    @staticmethod
    def _operation_event_row(row: sqlite3.Row) -> OperationAuditEvent:
        return OperationAuditEvent(
            event_id=row["event_id"], task_id=row["task_id"],
            operation_id=row["operation_id"], sequence=row["sequence"],
            from_status=row["from_status"], to_status=row["to_status"],
            actor_type=row["actor_type"], actor_id=row["actor_id"],
            principal_id=row["principal_id"], reason=row["reason"],
            created_at=row["created_at"],
        )

    def list_operation_events(self, operation_id: str, *, limit: int = 100,
                              before_event_id: int | None = None) -> list[OperationAuditEvent]:
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise ValueError("operation_id must be non-empty text.")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("limit must be an integer from 1 to 500.")
        if before_event_id is not None and (type(before_event_id) is not int or before_event_id < 1):
            raise ValueError("before_event_id must be a positive integer.")
        with self._connect() as db:
            if before_event_id is None:
                rows = db.execute(
                    "SELECT * FROM operation_events WHERE operation_id=? "
                    "ORDER BY sequence LIMIT ?", (operation_id.strip(), limit),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM operation_events WHERE operation_id=? AND event_id<? "
                    "ORDER BY sequence LIMIT ?", (operation_id.strip(), before_event_id, limit),
                ).fetchall()
            return [self._operation_event_row(row) for row in rows]

    def prepare_operation(self, task_id: str, *, idempotency_key: str, action: str,
                          request: dict[str, Any], principal_id: str | None = None) -> WorkflowOperation:
        """Persist intent before an external side effect; never executes the action."""
        for value, field, limit in ((task_id, "task_id", 200),
                                    (idempotency_key, "idempotency_key", _MAX_KEY_CHARS),
                                    (action, "action", _MAX_NAME_CHARS)):
            if not isinstance(value, str) or not value.strip() or len(value) > limit or "\\x00" in value:
                raise ValueError(f"{field} must be non-empty bounded text.")
        if principal_id is not None and (
            not isinstance(principal_id, str) or not principal_id.strip()
            or len(principal_id) > 200 or "\\x00" in principal_id
        ):
            raise ValueError("principal_id must be non-empty bounded text.")
        if not isinstance(request, dict):
            raise ValueError("request must be an object.")
        canonical = _bounded_json(request, 16 * 1024, "operation request")
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            task = db.execute("SELECT state FROM workflow_runs WHERE run_id=?", (task_id,)).fetchone()
            if task is None:
                raise KeyError("task not found.")
            if task["state"] != "running":
                raise WorkflowTransitionError("operations can only be prepared for running tasks.")
            existing = db.execute(
                "SELECT * FROM workflow_operations WHERE task_id=? AND idempotency_key=?",
                (task_id, idempotency_key.strip()),
            ).fetchone()
            if existing:
                if existing["action"] != action.strip() or existing["request_digest"] != digest:
                    raise WorkflowConflict("operation idempotency key was reused with a different request.")
                return self._operation_row(existing)
            operation_id = secrets.token_urlsafe(18)
            db.execute(
                "INSERT INTO workflow_operations(operation_id, task_id, idempotency_key, action, "
                "request_digest, status, version, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'prepared', 0, ?, ?)",
                (operation_id, task_id, idempotency_key.strip(), action.strip(), digest, now, now),
            )
            row = db.execute("SELECT * FROM workflow_operations WHERE operation_id=?",
                             (operation_id,)).fetchone()
            self._append_operation_event(
                db, operation=row, from_status=None, to_status="prepared",
                actor_type="system", actor_id="workflow_store",
                reason="operation_prepared", created_at=now, principal_id=principal_id,
            )
            return self._operation_row(row)

    def mark_operation_in_flight(self, operation_id: str, *, worker_id: str,
                                 expected_version: int, principal_id: str | None = None) -> WorkflowOperation:
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise ValueError("operation_id must be non-empty text.")
        if not isinstance(worker_id, str) or not worker_id.strip() or len(worker_id) > 200 or "\\x00" in worker_id:
            raise ValueError("worker_id must be non-empty bounded text.")
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("expected_version must be a non-negative integer.")
        if principal_id is not None and (
            not isinstance(principal_id, str) or not principal_id.strip()
            or len(principal_id) > 200 or "\\x00" in principal_id
        ):
            raise ValueError("principal_id must be non-empty bounded text.")
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT o.*, r.state AS task_state, r.lease_owner AS lease_owner, "
                "r.lease_expires_at AS lease_expires_at, r.definition_json AS definition_json, "
                "r.request_digest AS run_request_digest, r.approved_by AS approved_by, "
                "r.approved_at AS approved_at, r.approved_session_hash AS approved_session_hash, "
                "r.approval_digest AS approval_digest FROM workflow_operations o "
                "JOIN workflow_runs r ON r.run_id=o.task_id WHERE o.operation_id=?",
                (operation_id,),
            ).fetchone()
            if row is None:
                raise KeyError("operation not found.")
            if row["task_state"] != "running" or row["lease_owner"] != worker_id.strip():
                raise WorkflowTransitionError("worker does not own a running task for this operation.")
            definition = json.loads(row["definition_json"])
            if self._requires_approval(definition) and not (
                row["approved_by"] and row["approved_at"]
                and row["approved_session_hash"]
                and row["approval_digest"] == row["run_request_digest"]
            ):
                raise WorkflowTransitionError(
                    "workflow approval is missing or no longer matches the approved definition."
                )
            try:
                expiry = datetime.fromisoformat(row["lease_expires_at"])
                if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
                    raise WorkflowTransitionError("worker lease is expired or invalid.")
            except (TypeError, ValueError):
                raise WorkflowTransitionError("worker lease is expired or invalid.")
            if row["version"] != expected_version:
                raise WorkflowConflict("operation version changed; reload before dispatch.")
            if row["status"] != "prepared":
                raise WorkflowTransitionError("only prepared operations can be dispatched.")
            updated = db.execute(
                "UPDATE workflow_operations SET status='in_flight', version=version+1, updated_at=? "
                "WHERE operation_id=? AND version=? AND status='prepared'",
                (now, operation_id, expected_version),
            )
            if updated.rowcount != 1:
                raise WorkflowConflict("operation changed concurrently.")
            updated_row = db.execute(
                "SELECT * FROM workflow_operations WHERE operation_id=?", (operation_id,)
            ).fetchone()
            self._append_operation_event(
                db, operation=updated_row, from_status="prepared", to_status="in_flight",
                actor_type="worker", actor_id=worker_id.strip(),
                reason="provider_dispatch_started", created_at=now, principal_id=principal_id,
            )
            return self._operation_row(updated_row)

    def record_operation_outcome(self, operation_id: str, *, expected_version: int,
                                 status: str, provider_reference: str | None = None,
                                 outcome: dict[str, Any] | list[Any] | None = None,
                                 principal_id: str | None = None) -> WorkflowOperation:
        if status not in {"succeeded", "needs_reconciliation"}:
            raise ValueError("status must be succeeded or needs_reconciliation.")
        if principal_id is not None and (
            not isinstance(principal_id, str) or not principal_id.strip()
            or len(principal_id) > 200 or "\\x00" in principal_id
        ):
            raise ValueError("principal_id must be non-empty bounded text.")
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("expected_version must be a non-negative integer.")
        if provider_reference is not None and (
            not isinstance(provider_reference, str) or len(provider_reference) > 500 or "\\x00" in provider_reference
        ):
            raise ValueError("provider_reference must be bounded text.")
        outcome_json = _bounded_json(outcome, _MAX_RESULT_BYTES, "operation outcome") if outcome is not None else None
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM workflow_operations WHERE operation_id=?", (operation_id,)).fetchone()
            if row is None:
                raise KeyError("operation not found.")
            if row["version"] != expected_version:
                raise WorkflowConflict("operation version changed; reload before recording outcome.")
            if row["status"] != "in_flight":
                raise WorkflowTransitionError("only in-flight operations can record an outcome.")
            db.execute(
                "UPDATE workflow_operations SET status=?, provider_reference=?, outcome_json=?, "
                "version=version+1, updated_at=? WHERE operation_id=? AND version=?",
                (status, provider_reference, outcome_json, now, operation_id, expected_version),
            )
            updated_row = db.execute(
                "SELECT * FROM workflow_operations WHERE operation_id=?", (operation_id,)
            ).fetchone()
            self._append_operation_event(
                db, operation=updated_row, from_status="in_flight", to_status=status,
                actor_type="system", actor_id="workflow_store",
                reason="provider_outcome_recorded" if status == "succeeded" else "provider_outcome_uncertain",
                created_at=now, principal_id=principal_id,
            )
            return self._operation_row(updated_row)

    def get_operation(self, operation_id: str) -> WorkflowOperation | None:
        with self._connect() as db:
            return self._operation_row(db.execute(
                "SELECT * FROM workflow_operations WHERE operation_id=?", (operation_id,)
            ).fetchone())

    def reconcile_abandoned_operation(self, operation_id: str, *, expected_version: int,
                                      now: datetime | None = None,
                                      principal_id: str | None = None) -> WorkflowOperation:
        """Move an uncertain dispatch to reconciliation only after its task lease is gone.

        This is deliberately not a retry or provider lookup. An operator or a
        provider-specific reconciler must establish the external outcome.
        """
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise ValueError("operation_id must be non-empty text.")
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("expected_version must be a non-negative integer.")
        if principal_id is not None and (
            not isinstance(principal_id, str) or not principal_id.strip()
            or len(principal_id) > 200 or "\\x00" in principal_id
        ):
            raise ValueError("principal_id must be non-empty bounded text.")
        current_time = now or datetime.now(timezone.utc)
        if current_time.tzinfo is None:
            raise ValueError("now must be timezone-aware.")
        cutoff = current_time.astimezone(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT o.*, r.state AS task_state, r.lease_expires_at AS lease_expires_at "
                "FROM workflow_operations o JOIN workflow_runs r ON r.run_id=o.task_id "
                "WHERE o.operation_id=?", (operation_id.strip(),),
            ).fetchone()
            if row is None:
                raise KeyError("operation not found.")
            if row["version"] != expected_version:
                raise WorkflowConflict("operation version changed; reload before reconciliation.")
            if row["status"] != "in_flight":
                raise WorkflowTransitionError("only in-flight operations can be reconciled.")
            expiry = row["lease_expires_at"]
            lease_gone = row["task_state"] != "running" or expiry is None or expiry < cutoff
            if not lease_gone:
                raise WorkflowTransitionError("operation cannot be reconciled while its task lease is live.")
            updated = db.execute(
                "UPDATE workflow_operations SET status='needs_reconciliation', version=version+1, "
                "updated_at=? WHERE operation_id=? AND version=? AND status='in_flight'",
                (cutoff, operation_id.strip(), expected_version),
            )
            if updated.rowcount != 1:
                raise WorkflowConflict("operation changed concurrently.")
            updated_row = db.execute(
                "SELECT * FROM workflow_operations WHERE operation_id=?",
                (operation_id.strip(),),
            ).fetchone()
            self._append_operation_event(
                db, operation=updated_row, from_status="in_flight",
                to_status="needs_reconciliation", actor_type="system",
                actor_id="workflow_store", reason="expired_lease_requires_reconciliation",
                created_at=cutoff, principal_id=principal_id,
            )
            return self._operation_row(updated_row)

    def list_unresolved_operations(self, *, task_id: str | None = None, limit: int = 100):
        if task_id is not None and (not isinstance(task_id, str) or not task_id.strip()):
            raise ValueError("task_id must be non-empty text.")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("limit must be an integer from 1 to 500.")
        with self._connect() as db:
            if task_id is None:
                rows = db.execute(
                    "SELECT * FROM workflow_operations WHERE status IN ('in_flight', 'needs_reconciliation') "
                    "ORDER BY created_at LIMIT ?", (limit,),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM workflow_operations WHERE task_id=? "
                    "AND status IN ('in_flight', 'needs_reconciliation') ORDER BY created_at LIMIT ?",
                    (task_id, limit),
                ).fetchall()
            return [self._operation_row(row) for row in rows]

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
