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
import re
from pathlib import Path

from core.sqlite_file_security import prepare_private_sqlite_path
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
    "cancelled", "interrupted", "blocked",
})
_TERMINAL = frozenset({"succeeded", "cancelled", "interrupted", "blocked"})
_TRANSITIONS = {
    "queued": frozenset({"running", "cancelled"}),
    "running": frozenset({"waiting_approval", "succeeded", "failed", "cancelled", "interrupted"}),
    "waiting_approval": frozenset({"queued", "cancelled"}),
    "failed": frozenset({"queued", "cancelled"}),
    "succeeded": frozenset(),
    "cancelled": frozenset(),
    "interrupted": frozenset(),
    "blocked": frozenset(),
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
    agent_execution_spec_id: str | None = None
    agent_execution_spec_digest: str | None = None
    owner_id: str | None = None


class WorkflowRunStore:
    """SQLite lifecycle journal with idempotent creation and compare-and-swap updates."""

    def __init__(self, database_path: str):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path must be a non-empty path.")
        self.database_path = prepare_private_sqlite_path(database_path)
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
                    approved_session_hash TEXT,
                    agent_execution_spec_id TEXT,
                    agent_execution_spec_digest TEXT,
                    owner_id TEXT
                )
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(workflow_runs)")}
            for column in (
                "approved_by", "approved_at", "approval_digest", "approved_session_hash",
                "agent_execution_spec_id", "agent_execution_spec_digest", "owner_id",
            ):
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
                CREATE TABLE IF NOT EXISTS agent_execution_specs (
                    spec_id TEXT PRIMARY KEY,
                    version INTEGER NOT NULL,
                    spec_json TEXT NOT NULL,
                    spec_digest TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    owner_id TEXT
                )
            """)
            spec_columns = {
                row["name"] for row in db.execute("PRAGMA table_info(agent_execution_specs)")
            }
            if "owner_id" not in spec_columns:
                # Existing specs remain unowned; never infer an account from their tasks.
                db.execute("ALTER TABLE agent_execution_specs ADD COLUMN owner_id TEXT")
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_execution_specs_owner "
                "ON agent_execution_specs(owner_id, spec_id)"
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
                CREATE TABLE IF NOT EXISTS workflow_run_dependencies (
                    task_id TEXT NOT NULL,
                    depends_on_task_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(task_id, depends_on_task_id),
                    FOREIGN KEY(task_id) REFERENCES workflow_runs(run_id) ON DELETE CASCADE,
                    FOREIGN KEY(depends_on_task_id) REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
                    CHECK(task_id != depends_on_task_id)
                )
            """)
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_workflow_dependencies_prerequisite "
                "ON workflow_run_dependencies(depends_on_task_id, task_id)"
            )
            db.execute("""
                CREATE TABLE IF NOT EXISTS workflow_run_retries (
                    parent_task_id TEXT NOT NULL,
                    retry_task_id TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(parent_task_id, retry_task_id),
                    FOREIGN KEY(parent_task_id) REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
                    FOREIGN KEY(retry_task_id) REFERENCES workflow_runs(run_id) ON DELETE CASCADE,
                    CHECK(parent_task_id != retry_task_id)
                )
            """)
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_workflow_retries_parent "
                "ON workflow_run_retries(parent_task_id, retry_task_id)"
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

    def register_agent_execution_spec(
        self, spec: Any, *, owner_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist an immutable, owner-bound execution spec."""
        from automation.agent_execution_spec import AgentExecutionSpec
        if not isinstance(spec, AgentExecutionSpec):
            raise TypeError("spec must be an AgentExecutionSpec.")
        if owner_id is not None and (
            not isinstance(owner_id, str) or not owner_id.strip()
            or len(owner_id) > 200 or "\\x00" in owner_id
        ):
            raise ValueError("owner_id must be non-empty bounded text.")
        owner = owner_id.strip() if owner_id is not None else None
        value = spec.to_dict()
        encoded = _bounded_json(value, 16 * 1024, "agent execution spec")
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT spec_json, spec_digest, owner_id FROM agent_execution_specs WHERE spec_id=?",
                (spec.spec_id,),
            ).fetchone()
            if existing:
                if existing["spec_digest"] != digest or existing["owner_id"] != owner:
                    raise WorkflowConflict(
                        "execution spec ID is immutable and cannot be shared across owners."
                    )
                return json.loads(existing["spec_json"])
            db.execute(
                "INSERT INTO agent_execution_specs "
                "(spec_id, version, spec_json, spec_digest, created_at, updated_at, owner_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (spec.spec_id, spec.version, encoded, digest, now, now, owner),
            )
            return value

    def register_agent_execution_specs(
        self, specs: tuple[Any, ...], *, owner_id: str | None = None,
    ) -> tuple[dict[str, Any], ...]:
        """Atomically register an owner-bound dependency graph of immutable specs."""
        from automation.agent_execution_spec import AgentExecutionSpec, validate_execution_graph
        order = validate_execution_graph(specs)
        if owner_id is not None and (
            not isinstance(owner_id, str) or not owner_id.strip()
            or len(owner_id) > 200 or "\\x00" in owner_id
        ):
            raise ValueError("owner_id must be non-empty bounded text.")
        owner = owner_id.strip() if owner_id is not None else None
        by_id = {spec.spec_id: spec for spec in specs}
        encoded_by_id = {}
        for spec_id in order:
            spec = by_id[spec_id]
            encoded = _bounded_json(spec.to_dict(), 16 * 1024, "agent execution spec")
            encoded_by_id[spec_id] = (
                encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest(), spec.version,
            )
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for spec_id in order:
                encoded, digest, version = encoded_by_id[spec_id]
                existing = db.execute(
                    "SELECT spec_digest, owner_id FROM agent_execution_specs WHERE spec_id=?",
                    (spec_id,),
                ).fetchone()
                if existing is not None and (
                    existing["spec_digest"] != digest or existing["owner_id"] != owner
                ):
                    raise WorkflowConflict(
                        "execution spec ID is immutable and cannot be shared across owners."
                    )
            for spec_id in order:
                encoded, digest, version = encoded_by_id[spec_id]
                existing = db.execute(
                    "SELECT spec_digest FROM agent_execution_specs WHERE spec_id=?",
                    (spec_id,),
                ).fetchone()
                if existing is None:
                    db.execute(
                        "INSERT INTO agent_execution_specs "
                        "(spec_id, version, spec_json, spec_digest, created_at, updated_at, owner_id) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (spec_id, version, encoded, digest, now, now, owner),
                    )
            return tuple(by_id[spec_id].to_dict() for spec_id in order)

    def get_agent_execution_spec_record(
        self, spec_id: str, *, owner_id: str | None = None,
    ) -> tuple[dict[str, Any], str] | None:
        """Return an integrity-checked spec only to its exact owner."""
        if not isinstance(spec_id, str) or not spec_id.strip() or len(spec_id) > 80:
            raise ValueError("spec_id must be non-empty bounded text.")
        with self._connect() as db:
            row = db.execute(
                "SELECT spec_json, spec_digest FROM agent_execution_specs "
                "WHERE spec_id=? AND owner_id IS ?",
                (spec_id, owner_id),
            ).fetchone()
            if row is None:
                return None
            actual_digest = hashlib.sha256(row["spec_json"].encode("utf-8")).hexdigest()
            if actual_digest != row["spec_digest"]:
                raise WorkflowConflict("persisted agent specification failed its integrity check.")
            return json.loads(row["spec_json"]), row["spec_digest"]

    def get_agent_execution_spec(
        self, spec_id: str, *, owner_id: str | None = None,
    ) -> dict[str, Any] | None:
        record = self.get_agent_execution_spec_record(spec_id, owner_id=owner_id)
        return record[0] if record is not None else None

    def list_agent_execution_specs(
        self, *, owner_id: str | None = None, limit: int = 100,
    ) -> list[dict[str, Any]]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer from 1 to 100.")
        with self._connect() as db:
            rows = db.execute(
                "SELECT spec_id, spec_json, spec_digest FROM agent_execution_specs "
                "WHERE owner_id IS ? ORDER BY spec_id LIMIT ?",
                (owner_id, limit),
            ).fetchall()
            values = []
            for row in rows:
                actual_digest = hashlib.sha256(row["spec_json"].encode("utf-8")).hexdigest()
                if actual_digest != row["spec_digest"]:
                    raise WorkflowConflict(
                        f"persisted agent specification {row['spec_id']} failed its integrity check."
                    )
                values.append(json.loads(row["spec_json"]))
            return values

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
            agent_execution_spec_id=row["agent_execution_spec_id"],
            agent_execution_spec_digest=row["agent_execution_spec_digest"],
            owner_id=row["owner_id"],
        )

    def create(
        self, *, idempotency_key: str, workflow_name: str,
        definition: list[dict[str, Any]],
        agent_execution_spec_id: str | None = None,
        initial_event: dict[str, Any] | None = None,
        dependencies: tuple[str, ...] = (),
        retry_of_task_id: str | None = None,
        owner_id: str | None = None,
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
        if agent_execution_spec_id is not None and (
            not isinstance(agent_execution_spec_id, str)
            or not agent_execution_spec_id.strip()
            or len(agent_execution_spec_id) > 80
            or "\x00" in agent_execution_spec_id
        ):
            raise ValueError("agent_execution_spec_id must be bounded non-empty text.")
        spec_id = agent_execution_spec_id.strip() if agent_execution_spec_id is not None else None
        if owner_id is not None and (
            not isinstance(owner_id, str) or not owner_id.strip()
            or len(owner_id) > 200 or "\x00" in owner_id
        ):
            raise ValueError("owner_id must be non-empty bounded text when provided.")
        owner = owner_id.strip() if owner_id is not None else None
        if not isinstance(dependencies, tuple) or len(dependencies) > 32:
            raise ValueError("dependencies must be a tuple containing at most 32 task IDs.")
        if any(
            not isinstance(item, str) or not item.strip() or len(item) > 200 or "\x00" in item
            for item in dependencies
        ):
            raise ValueError("dependency task IDs must be non-empty bounded text.")
        dependency_ids = tuple(item.strip() for item in dependencies)
        if len(set(dependency_ids)) != len(dependency_ids):
            raise ValueError("dependency task IDs must be unique.")
        retry_parent = retry_of_task_id.strip() if isinstance(retry_of_task_id, str) else retry_of_task_id
        if retry_parent is not None and (
            not isinstance(retry_parent, str) or not retry_parent
            or len(retry_parent) > 200 or "\x00" in retry_parent
        ):
            raise ValueError("retry_of_task_id must be non-empty bounded text.")
        if initial_event is not None and (
            not isinstance(initial_event, dict)
            or set(initial_event) != {"kind", "actor_id", "payload"}
        ):
            raise ValueError("initial_event must contain exactly kind, actor_id, and payload.")
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            spec_digest = None
            if spec_id is not None:
                spec_row = db.execute(
                    "SELECT spec_json, spec_digest FROM agent_execution_specs WHERE spec_id=?",
                    (spec_id,),
                ).fetchone()
                if spec_row is None:
                    raise KeyError("agent execution specification not found.")
                spec_owner = db.execute(
                    "SELECT owner_id FROM agent_execution_specs WHERE spec_id=?",
                    (spec_id,),
                ).fetchone()["owner_id"]
                if spec_owner != owner:
                    raise WorkflowConflict("agent execution specification must belong to the task owner.")
                actual_spec_digest = hashlib.sha256(
                    spec_row["spec_json"].encode("utf-8")
                ).hexdigest()
                if actual_spec_digest != spec_row["spec_digest"]:
                    raise WorkflowConflict(
                        "persisted agent specification failed its integrity check."
                    )
                try:
                    spec_value = json.loads(spec_row["spec_json"])
                except (TypeError, ValueError) as exc:
                    raise WorkflowConflict(
                        "persisted agent specification is not valid JSON."
                    ) from exc
                if spec_value.get("role") != name:
                    raise WorkflowTransitionError("task role does not match the registered agent specification.")
                if "text" not in spec_value.get("output_types", []):
                    raise WorkflowTransitionError("registered agent specification does not permit text output.")
                spec_digest = spec_row["spec_digest"]
            for dependency_id in dependency_ids:
                dependency_row = db.execute(
                    "SELECT owner_id FROM workflow_runs WHERE run_id=?", (dependency_id,)
                ).fetchone()
                if dependency_row is None:
                    raise KeyError("dependency task not found.")
                # Dependency edges are account-local even for legacy ownerless
                # records. Never let a NULL-owner task bridge into an account's graph.
                if dependency_row["owner_id"] != owner:
                    raise WorkflowConflict("task dependencies must belong to the same owner.")
            if retry_parent is not None:
                retry_source = db.execute(
                    "SELECT state, workflow_name, definition_json, owner_id FROM workflow_runs WHERE run_id=?",
                    (retry_parent,),
                ).fetchone()
                if retry_source is None:
                    raise KeyError("retry source task not found.")
                if retry_source["state"] not in {"failed", "interrupted"}:
                    raise WorkflowTransitionError(
                        "only failed or interrupted tasks can have retry lineage."
                    )
                if retry_source["owner_id"] != owner:
                    raise WorkflowConflict("retry task must preserve its source owner.")
                if (
                    retry_source["workflow_name"] != name
                    or retry_source["definition_json"] != definition_json
                ):
                    raise WorkflowConflict(
                        "retry task must preserve its source workflow definition."
                    )
            digest_input = (
                name + "\n" + definition_json
                + (("\n" + spec_id) if spec_id else "")
                + (
                    "\n" + json.dumps(dependency_ids, separators=(",", ":"))
                    if dependency_ids else ""
                )
                + (("\nretry:" + retry_parent) if retry_parent else "")
            )
            if owner is not None:
                digest_input += "\nowner:" + owner
            digest = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()
            existing = db.execute(
                "SELECT * FROM workflow_runs WHERE idempotency_key=?", (key,)
            ).fetchone()
            if existing:
                if existing["request_digest"] != digest:
                    raise WorkflowConflict("idempotency key was already used for a different workflow or agent specification.")
                if initial_event is not None:
                    event_exists = db.execute(
                        "SELECT 1 FROM task_events WHERE task_id=? AND kind=? LIMIT 1",
                        (existing["run_id"], initial_event["kind"]),
                    ).fetchone()
                    if event_exists is None:
                        self._append_task_event_in_transaction(
                            db, existing["run_id"], kind=initial_event["kind"],
                            actor_id=initial_event["actor_id"], payload=initial_event["payload"],
                            expected_version=existing["version"], now=now,
                        )
                return self._row(db.execute(
                    "SELECT * FROM workflow_runs WHERE run_id=?", (existing["run_id"],)
                ).fetchone())
            run_id = secrets.token_urlsafe(18)
            db.execute("""
                INSERT INTO workflow_runs (
                    run_id, idempotency_key, request_digest, workflow_name,
                    definition_json, state, created_at, updated_at,
                    agent_execution_spec_id, agent_execution_spec_digest, owner_id
                ) VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?)
            """, (run_id, key, digest, name, definition_json, now, now, spec_id, spec_digest, owner))
            for dependency_id in dependency_ids:
                db.execute(
                    "INSERT INTO workflow_run_dependencies(task_id, depends_on_task_id, created_at) "
                    "VALUES (?, ?, ?)",
                    (run_id, dependency_id, now),
                )
            if retry_parent is not None:
                db.execute(
                    "INSERT INTO workflow_run_retries(parent_task_id, retry_task_id, created_at) "
                    "VALUES (?, ?, ?)",
                    (retry_parent, run_id, now),
                )
            if initial_event is not None:
                self._append_task_event_in_transaction(
                    db, run_id, kind=initial_event["kind"],
                    actor_id=initial_event["actor_id"], payload=initial_event["payload"],
                    expected_version=0, now=now,
                )
            return self._row(db.execute(
                "SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)
            ).fetchone())

    @staticmethod
    def _spec_requires_approval(db: sqlite3.Connection, spec_id: str | None) -> bool:
        if spec_id is None:
            return False
        row = db.execute(
            "SELECT spec_json, spec_digest FROM agent_execution_specs WHERE spec_id=?", (spec_id,)
        ).fetchone()
        if row is None:
            # A task with a broken agent reference must never be dispatched.
            return True
        actual_digest = hashlib.sha256(row["spec_json"].encode("utf-8")).hexdigest()
        if actual_digest != row["spec_digest"]:
            # Corrupt specs are never allowed to lower an approval requirement.
            return True
        try:
            spec_value = json.loads(row["spec_json"])
        except (TypeError, ValueError):
            # Malformed policy data must never make an agent task executable.
            return True
        if not isinstance(spec_value, dict):
            return True
        policy = spec_value.get("approval_policy", "required")
        if policy not in {"required", "per_action", "not_requested"}:
            return True
        return policy in {"required", "per_action"}

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

    def get_dependencies(self, task_id: str) -> tuple[str, ...]:
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("task_id must be non-empty text.")
        with self._connect() as db:
            rows = db.execute(
                "SELECT depends_on_task_id FROM workflow_run_dependencies "
                "WHERE task_id=? ORDER BY depends_on_task_id",
                (task_id,),
            ).fetchall()
            return tuple(row["depends_on_task_id"] for row in rows)

    def claim_next(
        self, *, worker_id: str, lease_seconds: int = 60,
        worker_session: tuple[str, str] | None = None,
    ) -> WorkflowRun | None:
        """Atomically claim the oldest queued run; never steals or replays running work."""
        if not isinstance(worker_id, str) or not worker_id.strip() or len(worker_id) > 200 or "\x00" in worker_id:
            raise ValueError("worker_id must be non-empty bounded text.")
        if worker_session is not None and (
            not isinstance(worker_session, tuple) or len(worker_session) != 2
        ):
            raise ValueError("worker_session must be a (worker_id, token) tuple.")
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be an integer from 1 to 3600.")
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        lease_expires = (now_dt + timedelta(seconds=lease_seconds)).isoformat()
        with self._connect() as db:
            # Serialize session fencing, selection, and claim in one transaction.
            # The worker registry must share this database file.
            db.execute("BEGIN IMMEDIATE")
            if worker_session is not None:
                session_worker_id, session_token = worker_session
                if not isinstance(session_worker_id, str) or not session_worker_id.strip() or "\x00" in session_worker_id:
                    raise ValueError("worker session identity must be non-empty bounded text.")
                if session_worker_id != worker_id:
                    raise WorkflowTransitionError("worker session identity does not match claim identity.")
                if not isinstance(session_token, str) or not session_token or len(session_token) > 256 or "\x00" in session_token:
                    raise ValueError("worker session token must be bounded non-empty text.")
                session_hash = hashlib.sha256(session_token.encode("utf-8")).hexdigest()
                current_session = db.execute(
                    "SELECT metadata_json FROM automation_workers "
                    "WHERE worker_id=? AND session_hash=? AND status='active'",
                    (worker_id, session_hash),
                ).fetchone()
                if current_session is None:
                    raise WorkflowTransitionError("worker registry session is not current or active.")
                # Parallel child sessions are independently token-fenced, but
                # must also remain subordinate to the exact parent process
                # session that created them. Validate both identities inside
                # this same BEGIN IMMEDIATE transaction as task selection/claim.
                try:
                    worker_metadata = json.loads(current_session["metadata_json"])
                except (TypeError, ValueError):
                    raise WorkflowTransitionError("worker registry metadata is invalid.")
                if worker_metadata.get("role") == "specialist_pool_child":
                    parent_worker_id = worker_metadata.get("parent_worker_id")
                    parent_session_hash = worker_metadata.get("parent_session_hash")
                    if (
                        not isinstance(parent_worker_id, str) or not parent_worker_id.strip()
                        or not isinstance(parent_session_hash, str)
                        or not re.fullmatch(r"[0-9a-f]{64}", parent_session_hash)
                    ):
                        raise WorkflowTransitionError("child worker parent session binding is invalid.")
                    parent_session = db.execute(
                        "SELECT 1 FROM automation_workers "
                        "WHERE worker_id=? AND session_hash=? AND status='active'",
                        (parent_worker_id, parent_session_hash),
                    ).fetchone()
                    if parent_session is None:
                        raise WorkflowTransitionError("child worker parent session is no longer current or active.")
            # Terminally failed prerequisites block dependants; retryable failed
            # prerequisites remain pending so an operator can retry them first.
            blocked = db.execute("""
                SELECT r.run_id, r.version
                FROM workflow_runs r
                WHERE r.state='queued' AND r.owner_id IS NOT NULL AND EXISTS (
                    SELECT 1 FROM workflow_run_dependencies d
                    WHERE d.task_id=r.run_id AND NOT EXISTS (
                        WITH RECURSIVE lineage(task_id) AS (
                            SELECT d.depends_on_task_id
                            UNION
                            SELECT rr.retry_task_id FROM workflow_run_retries rr
                            JOIN lineage l ON rr.parent_task_id=l.task_id
                        )
                        SELECT 1 FROM lineage l JOIN workflow_runs p ON p.run_id=l.task_id
                        WHERE p.state NOT IN ('cancelled', 'blocked')
                    )
                )
                ORDER BY r.created_at, r.run_id LIMIT 100
            """).fetchall()
            for candidate in blocked:
                updated = db.execute(
                    "UPDATE workflow_runs SET state='blocked', error_code='dependency_terminal', "
                    "updated_at=?, version=version+1 WHERE run_id=? AND version=? AND state='queued'",
                    (now, candidate["run_id"], candidate["version"]),
                )
                if updated.rowcount == 1:
                    self._append_task_event_in_transaction(
                        db, candidate["run_id"], kind="task.blocked",
                        actor_id="workflow-system",
                        payload={"reason": "dependency_terminal"},
                        expected_version=candidate["version"] + 1, now=now,
                    )
            rows = db.execute("""
                SELECT * FROM workflow_runs r WHERE r.state='queued'
                AND r.owner_id IS NOT NULL
                AND NOT EXISTS (
                    SELECT 1 FROM workflow_run_dependencies d
                    WHERE d.task_id=r.run_id AND NOT EXISTS (
                        WITH RECURSIVE lineage(task_id) AS (
                            SELECT d.depends_on_task_id
                            UNION
                            SELECT rr.retry_task_id FROM workflow_run_retries rr
                            JOIN lineage l ON rr.parent_task_id=l.task_id
                        )
                        SELECT 1 FROM lineage l JOIN workflow_runs p ON p.run_id=l.task_id
                        WHERE p.state='succeeded'
                    )
                )
                ORDER BY r.created_at, r.run_id LIMIT 100
            """).fetchall()
            row = next((
                candidate for candidate in rows
                if (
                    (
                        not self._requires_approval(json.loads(candidate["definition_json"]))
                        and not self._spec_requires_approval(db, candidate["agent_execution_spec_id"])
                    )
                    or (
                        candidate["approved_by"] and candidate["approved_at"]
                        and candidate["approved_session_hash"]
                        and candidate["approval_digest"] == candidate["request_digest"]
                    )
                )
            ), None)
            if row is None:
                return None
            updated = db.execute("""
                UPDATE workflow_runs SET state='running', updated_at=?,
                    version=version+1, attempts=attempts+1, lease_owner=?,
                    lease_expires_at=?
                WHERE run_id=? AND version=? AND state='queued' AND owner_id IS NOT NULL
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
        if not isinstance(run_id, str) or not run_id.strip() or len(run_id) > 200 or "\x00" in run_id:
            raise ValueError("run_id must be non-empty bounded text.")
        if not isinstance(worker_id, str) or not worker_id.strip() or len(worker_id) > 200 or "\x00" in worker_id:
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

    def get_owned(self, run_id: str, *, owner_id: str) -> WorkflowRun | None:
        """Fetch a task only when its persisted immutable owner matches."""
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("run_id must be non-empty text.")
        if not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 200 or chr(0) in owner_id:
            raise ValueError("owner_id must be non-empty bounded text.")
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM workflow_runs WHERE run_id=? AND owner_id=?",
                (run_id, owner_id.strip()),
            ).fetchone()
            return self._row(row) if row else None

    def list_owned_runs(
        self, *, owner_id: str, state: str | None = None, limit: int = 50,
    ) -> list[WorkflowRun]:
        """List only records belonging to one immutable owner; legacy rows are excluded."""
        if not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 200 or chr(0) in owner_id:
            raise ValueError("owner_id must be non-empty bounded text.")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer from 1 to 100.")
        if state is not None and state not in _STATES:
            raise ValueError("Unknown workflow state.")
        with self._connect() as db:
            if state is None:
                rows = db.execute(
                    "SELECT * FROM workflow_runs WHERE owner_id=? ORDER BY created_at DESC LIMIT ?",
                    (owner_id.strip(), limit),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM workflow_runs WHERE owner_id=? AND state=? ORDER BY created_at DESC LIMIT ?",
                    (owner_id.strip(), state, limit),
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

    def list_queued_approved_by_session(
        self, session_hash: str, *, limit: int = 100,
        after: tuple[str, str] | None = None,
    ) -> list[WorkflowRun]:
        """Page queued tasks approved by one exact session without exposing other tasks."""
        if not isinstance(session_hash, str) or len(session_hash) != 64 or any(
            char not in "0123456789abcdef" for char in session_hash
        ):
            raise ValueError("session_hash must be a lowercase SHA-256 digest.")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer from 1 to 100.")
        if after is not None and (
            not isinstance(after, tuple) or len(after) != 2
            or not all(isinstance(value, str) and value for value in after)
        ):
            raise ValueError("after must be a (created_at, run_id) cursor.")
        with self._connect() as db:
            if after is None:
                rows = db.execute(
                    "SELECT * FROM workflow_runs WHERE state='queued' "
                    "AND approved_session_hash=? AND approved_by IS NOT NULL "
                    "ORDER BY created_at, run_id LIMIT ?",
                    (session_hash, limit),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM workflow_runs WHERE state='queued' "
                    "AND approved_session_hash=? AND approved_by IS NOT NULL "
                    "AND (created_at>? OR (created_at=? AND run_id>?)) "
                    "ORDER BY created_at, run_id LIMIT ?",
                    (session_hash, after[0], after[0], after[1], limit),
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
            if to_state == "running" and (
                self._requires_approval(current.definition)
                or self._spec_requires_approval(db, row["agent_execution_spec_id"])
            ):
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


    @staticmethod
    def _append_task_event_in_transaction(
        db: sqlite3.Connection, task_id: str, *, kind: str, actor_id: str,
        payload: dict[str, Any], expected_version: int, now: str,
    ):
        """Insert one event and advance its task version inside the caller's transaction."""
        from automation.task_runtime import TaskEvent, _EVENT_KINDS
        if kind not in _EVENT_KINDS:
            raise ValueError("Unknown task event kind.")
        if not isinstance(actor_id, str) or not actor_id.strip() or len(actor_id) > 200 or "\x00" in actor_id:
            raise ValueError("actor_id must be non-empty bounded text.")
        if not isinstance(payload, dict):
            raise ValueError("task event payload must be an object.")
        payload_json = _bounded_json(payload, 8 * 1024, "task event payload")
        row = db.execute("SELECT * FROM workflow_runs WHERE run_id=?", (task_id,)).fetchone()
        if row is None:
            raise KeyError("task not found.")
        if row["version"] != expected_version:
            raise WorkflowConflict("task version changed; reload before appending an event.")
        if row["state"] in _TERMINAL:
            expected_terminal_event = {
                "succeeded": "task.completed", "failed": "task.failed",
                "cancelled": "task.cancelled", "interrupted": "task.interrupted",
                "blocked": "task.blocked",
            }.get(row["state"])
            if kind != expected_terminal_event:
                raise WorkflowTransitionError("cannot append events to a terminal task.")
            prior_terminal = db.execute(
                "SELECT 1 FROM task_events WHERE task_id=? AND kind IN "
                "('task.completed', 'task.failed', 'task.cancelled', 'task.interrupted', 'task.blocked') LIMIT 1",
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

    def approve_with_event(
        self, run_id: str, *, actor_id: str, session_id: str, expected_version: int,
    ) -> WorkflowRun:
        """Commit session approval and its audit event as one SQLite transaction."""
        if not isinstance(actor_id, str) or not actor_id.strip() or len(actor_id) > 200 or chr(0) in actor_id:
            raise ValueError("actor_id must be non-empty bounded text.")
        if not isinstance(session_id, str) or not session_id.strip() or len(session_id) > 512 or chr(0) in session_id:
            raise ValueError("session_id must be non-empty bounded text.")
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("expected_version must be a non-negative integer.")
        session_hash = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
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
            self._append_task_event_in_transaction(
                db, run_id, kind="task.approved", actor_id=actor_id,
                payload={"state_version": expected_version + 1},
                expected_version=expected_version + 1, now=now,
            )
            return self._row(db.execute(
                "SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)
            ).fetchone())

    def recover_expired_with_events(
        self, *, actor_id: str, limit: int = 50,
    ) -> list[WorkflowRun]:
        """Atomically interrupt expired leases and append their audit events."""
        if not isinstance(actor_id, str) or not actor_id.strip() or len(actor_id) > 200 or "\x00" in actor_id:
            raise ValueError("actor_id must be non-empty bounded text.")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer from 1 to 100.")
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        changed: list[WorkflowRun] = []
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT * FROM workflow_runs WHERE state='running' "
                "AND lease_expires_at IS NOT NULL AND lease_expires_at < ? "
                "ORDER BY lease_expires_at LIMIT ?",
                (now, limit),
            ).fetchall()
            for row in rows:
                updated = db.execute(
                    "UPDATE workflow_runs SET state='interrupted', error_code='lease_expired', "
                    "updated_at=?, version=version+1, lease_owner=NULL, lease_expires_at=NULL "
                    "WHERE run_id=? AND version=? AND state='running' "
                    "AND lease_owner=? AND lease_expires_at=? AND lease_expires_at < ?",
                    (
                        now, row["run_id"], row["version"], row["lease_owner"],
                        row["lease_expires_at"], now,
                    ),
                )
                if updated.rowcount != 1:
                    continue
                self._append_task_event_in_transaction(
                    db, row["run_id"], kind="task.interrupted", actor_id=actor_id,
                    payload={"state": "interrupted", "error_code": "lease_expired",
                             "automatic_replay": False, "state_version": row["version"] + 2},
                    expected_version=row["version"] + 1, now=now,
                )
                changed_row = db.execute(
                    "SELECT * FROM workflow_runs WHERE run_id=?", (row["run_id"],)
                ).fetchone()
                changed.append(self._row(changed_row))
        return changed

    def transition_with_event(
        self, run_id: str, *, expected_version: int, to_state: str,
        event_kind: str, actor_id: str, event_payload: dict[str, Any],
        result: dict[str, Any] | list[Any] | None = None,
        error_code: str | None = None,
        expected_lease_owner: str | None = None,
    ) -> WorkflowRun:
        """Commit a state transition and its audit event atomically."""
        if to_state not in _STATES:
            raise ValueError("Unknown workflow state.")
        terminal_events = {
            "succeeded": "task.completed", "failed": "task.failed",
            "cancelled": "task.cancelled", "interrupted": "task.interrupted",
            "blocked": "task.blocked",
        }
        expected_event = terminal_events.get(to_state)
        if (expected_event is not None and event_kind != expected_event) or (
            expected_event is None and event_kind in terminal_events.values()
        ):
            raise ValueError("event_kind must match the resulting terminal state.")
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("expected_version must be a non-negative integer.")
        if error_code is not None and (
            not isinstance(error_code, str) or not error_code or len(error_code) > 100
            or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for ch in error_code)
        ):
            raise ValueError("error_code must be a bounded machine-readable code.")
        result_json = None if result is None else _bounded_json(result, _MAX_RESULT_BYTES, "result")
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                raise KeyError("workflow run not found.")
            current = self._row(row)
            if current.version != expected_version:
                raise WorkflowConflict("workflow run version changed; reload before updating.")
            if expected_lease_owner is not None:
                if (
                    not isinstance(expected_lease_owner, str)
                    or not expected_lease_owner.strip()
                    or len(expected_lease_owner) > 200
                    or chr(0) in expected_lease_owner
                ):
                    raise ValueError("expected_lease_owner must be non-empty bounded text.")
                if (
                    row["state"] != "running"
                    or row["lease_owner"] != expected_lease_owner.strip()
                    or not row["lease_expires_at"]
                    or row["lease_expires_at"] <= now
                ):
                    raise WorkflowConflict("active worker lease is required for this transition.")
            if to_state == "running" and (
                self._requires_approval(current.definition)
                or self._spec_requires_approval(db, row["agent_execution_spec_id"])
            ):
                if not (row["approved_by"] and row["approved_at"] and row["approved_session_hash"]
                        and row["approval_digest"] == row["request_digest"]):
                    raise WorkflowTransitionError("workflow requires explicit approval before execution.")
            if to_state not in _TRANSITIONS[current.state]:
                raise WorkflowTransitionError(f"transition {current.state!r} -> {to_state!r} is not allowed.")
            updated = db.execute("""
                UPDATE workflow_runs SET state=?, result_json=?, error_code=?,
                    updated_at=?, version=version+1, lease_owner=NULL, lease_expires_at=NULL
                WHERE run_id=? AND version=?
            """, (to_state, result_json, error_code, now, run_id, expected_version))
            if updated.rowcount != 1:
                raise WorkflowConflict("workflow run changed concurrently.")
            self._append_task_event_in_transaction(
                db, run_id, kind=event_kind, actor_id=actor_id,
                payload=event_payload, expected_version=expected_version + 1, now=now,
            )
            return self._row(db.execute(
                "SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)
            ).fetchone())

    def append_task_event(
        self, task_id: str, *, kind: str, actor_id: str,
        payload: dict[str, Any], expected_version: int,
        expected_lease_owner: str | None = None,
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
        lease_bound_kinds = {
            "task.progress", "task.artifact", "task.sandbox.started",
            "task.sandbox.completed", "task.sandbox.cancelled", "task.sandbox.failed",
        }
        if kind in lease_bound_kinds and expected_lease_owner is None:
            raise ValueError("worker lifecycle events require an expected lease owner.")
        payload_json = _bounded_json(payload, 8 * 1024, "task event payload")
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM workflow_runs WHERE run_id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError("task not found.")
            if row["version"] != expected_version:
                raise WorkflowConflict("task version changed; reload before appending an event.")
            if expected_lease_owner is not None:
                if (
                    not isinstance(expected_lease_owner, str)
                    or not expected_lease_owner.strip()
                    or len(expected_lease_owner) > 200
                    or chr(0) in expected_lease_owner
                ):
                    raise ValueError("expected_lease_owner must be non-empty bounded text.")
                if row["state"] != "running" or row["lease_owner"] != expected_lease_owner.strip():
                    raise WorkflowConflict("task lease is no longer owned by this worker.")
                if not row["lease_expires_at"] or row["lease_expires_at"] <= now:
                    raise WorkflowConflict("task lease expired before lifecycle event was recorded.")
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
