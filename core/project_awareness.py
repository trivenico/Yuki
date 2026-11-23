"""Persistent, owner-scoped project awareness with verified outcome provenance.

Records are append-only advisory history. They are never executable instructions.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import sqlite3
from itertools import islice
from typing import Callable, Iterable
from uuid import uuid4

from core.sqlite_file_security import prepare_private_sqlite_path

_EVENT_KINDS = frozenset({
    "goal_set", "milestone_recorded", "task_outcome_verified",
    "risk_recorded", "planning_decision",
})
_MAX_SUMMARY = 1200
_MAX_SELECTED = 20


def _required(value: str, field: str, limit: int = 200) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
        raise ValueError(f"{field} must be non-empty bounded text.")
    if any(ord(ch) < 32 and ch not in "\n\t" for ch in value):
        raise ValueError(f"{field} contains control characters.")
    return value.strip()


@dataclass(frozen=True, slots=True)
class ProjectAwarenessEvent:
    event_id: str
    project_id: str
    owner_id: str
    event_kind: str
    summary: str
    task_id: str | None
    task_version: int | None
    evidence_ref: str | None
    selected_task_ids: tuple[str, ...]
    created_at: str


class ProjectAwarenessStore:
    """Append-only project memory; authorization and proof verification are host-owned."""

    def __init__(
        self, database_path: str, *,
        project_authorizer: Callable[[str, str], bool],
        task_outcome_verifier: Callable[[str, str, str, int, str], bool],
    ):
        if not callable(project_authorizer) or not callable(task_outcome_verifier):
            raise ValueError("project_authorizer and task_outcome_verifier are required.")
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path must be a non-empty path.")
        self.database_path = prepare_private_sqlite_path(database_path)
        self._authorize = project_authorizer
        self._verify_outcome = task_outcome_verifier
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS project_awareness_events (
                    event_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    event_kind TEXT NOT NULL CHECK(event_kind IN
                      ('goal_set','milestone_recorded','task_outcome_verified','risk_recorded','planning_decision')),
                    summary TEXT NOT NULL,
                    task_id TEXT,
                    task_version INTEGER,
                    evidence_ref TEXT,
                    selected_task_ids TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    CHECK((task_id IS NULL AND task_version IS NULL) OR
                          (task_id IS NOT NULL AND task_version > 0)),
                    CHECK(event_kind != 'task_outcome_verified' OR
                          (task_id IS NOT NULL AND task_version IS NOT NULL AND evidence_ref IS NOT NULL))
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_project_awareness_recent ON project_awareness_events(owner_id, project_id, created_at)")
            db.execute("""
                CREATE TRIGGER IF NOT EXISTS project_awareness_no_update
                BEFORE UPDATE ON project_awareness_events BEGIN
                    SELECT RAISE(ABORT, 'project awareness is append-only');
                END
            """)
            db.execute("""
                CREATE TRIGGER IF NOT EXISTS project_awareness_no_delete
                BEFORE DELETE ON project_awareness_events BEGIN
                    SELECT RAISE(ABORT, 'project awareness is append-only');
                END
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

    def append(
        self, *, owner_id: str, project_id: str, event_kind: str, summary: str,
        task_id: str | None = None, task_version: int | None = None,
        evidence_ref: str | None = None, selected_task_ids: Iterable[str] = (),
    ) -> ProjectAwarenessEvent:
        owner = _required(owner_id, "owner_id")
        project = _required(project_id, "project_id")
        kind = _required(event_kind, "event_kind", 40)
        if kind not in _EVENT_KINDS:
            raise ValueError("unsupported project-awareness event kind.")
        note = _required(summary, "summary", _MAX_SUMMARY)
        task = _required(task_id, "task_id") if task_id is not None else None
        proof = _required(evidence_ref, "evidence_ref", 500) if evidence_ref is not None else None
        if task_version is not None and (type(task_version) is not int or task_version < 1):
            raise ValueError("task_version must be a positive integer.")
        if (task is None) != (task_version is None):
            raise ValueError("task_id and task_version must be supplied together.")
        try:
            authorized = self._authorize(owner, project)
        except Exception as exc:
            raise PermissionError("project access could not be verified.") from exc
        if authorized is not True:
            raise PermissionError("project is not accessible to this owner.")
        if kind == "task_outcome_verified":
            if task is None or proof is None:
                raise ValueError("verified task outcomes require task version and evidence reference.")
            try:
                trusted = self._verify_outcome(owner, project, task, task_version, proof)
            except Exception as exc:
                raise PermissionError("task outcome evidence could not be verified.") from exc
            if trusted is not True:
                raise PermissionError("task outcome requires trusted human-review evidence.")
        elif proof is not None:
            raise ValueError("evidence_ref is reserved for verified task outcomes.")
        if isinstance(selected_task_ids, (str, bytes)):
            raise ValueError("selected_task_ids must be a collection of task IDs.")
        try:
            selected = tuple(_required(item, "selected_task_id", 200) for item in islice(iter(selected_task_ids), _MAX_SELECTED + 1))
        except TypeError as exc:
            raise ValueError("selected_task_ids must be iterable.") from exc
        if len(selected) > _MAX_SELECTED or len(set(selected)) != len(selected):
            raise ValueError("selected_task_ids must be unique and limited to 20.")
        if kind != "planning_decision" and selected:
            raise ValueError("selected_task_ids are only valid for planning decisions.")
        event_id = "paw_" + uuid4().hex
        created = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO project_awareness_events VALUES(?,?,?,?,?,?,?,?,?,?)",
                (event_id, project, owner, kind, note, task, task_version, proof,
                 json.dumps(selected, separators=(",", ":")), created),
            )
        return ProjectAwarenessEvent(event_id, project, owner, kind, note, task,
                                     task_version, proof, selected, created)

    def list_recent(self, *, owner_id: str, project_id: str, limit: int = 50) -> tuple[ProjectAwarenessEvent, ...]:
        owner = _required(owner_id, "owner_id")
        project = _required(project_id, "project_id")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer from 1 to 100.")
        try:
            authorized = self._authorize(owner, project)
        except Exception as exc:
            raise PermissionError("project access could not be verified.") from exc
        if authorized is not True:
            raise PermissionError("project is not accessible to this owner.")
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM project_awareness_events WHERE owner_id=? AND project_id=? ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (owner, project, limit),
            ).fetchall()
        return tuple(ProjectAwarenessEvent(
            row["event_id"], row["project_id"], row["owner_id"], row["event_kind"],
            row["summary"], row["task_id"], row["task_version"], row["evidence_ref"],
            tuple(json.loads(row["selected_task_ids"])), row["created_at"],
        ) for row in rows)


def build_project_awareness_store(database_path: str, *, registry, evidence_store, task_runtime) -> ProjectAwarenessStore:
    """Compose awareness with the authenticated project registry and evidence ledger."""
    from core.project_registry import ProjectRegistry
    from core.task_evidence import TaskEvidenceStore
    from automation.task_runtime import TaskRuntime

    if not isinstance(registry, ProjectRegistry):
        raise TypeError("registry must be a ProjectRegistry.")
    if not isinstance(evidence_store, TaskEvidenceStore):
        raise TypeError("evidence_store must be a TaskEvidenceStore.")
    if not isinstance(task_runtime, TaskRuntime):
        raise TypeError("task_runtime must be a TaskRuntime.")

    def authorize(owner_id: str, project_id: str) -> bool:
        registry.list_task_ids(owner_id=owner_id, project_id=project_id, limit=1)
        return True

    def verify_human_review(owner_id: str, project_id: str, task_id: str, task_version: int, reference: str) -> bool:
        if task_id not in registry.list_task_ids(owner_id=owner_id, project_id=project_id, limit=500):
            return False
        task = task_runtime.get(actor_id=owner_id, task_id=task_id)
        if task.owner_id != owner_id or task.version != task_version:
            return False
        records = evidence_store.list_verified(
            owner_id=owner_id, task_id=task_id, task_version=task_version, limit=100,
        )
        return any(record.kind == "human_review" and record.reference == reference for record in records)

    return ProjectAwarenessStore(
        database_path, project_authorizer=authorize,
        task_outcome_verifier=verify_human_review,
    )
