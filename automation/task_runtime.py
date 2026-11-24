"""Unified task facade over Yuki's durable workflow journal.

This service records task state and progress; it does not execute arbitrary
code or automatically replay work after an expired worker lease.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import re
from typing import Any, Callable

from automation.workflow_store import WorkflowConflict, WorkflowRun, WorkflowRunStore, WorkflowTransitionError

_EVENT_KINDS = frozenset({"task.created", "task.approved", "task.progress", "task.artifact", "task.completed", "task.failed", "task.cancelled", "task.interrupted"})
_CODE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")


@dataclass(frozen=True)
class TaskEvent:
    event_id: int
    task_id: str
    sequence: int
    kind: str
    actor_id: str
    payload: dict[str, Any]
    created_at: str


@dataclass(frozen=True)
class TaskSummary:
    """Redacted task metadata for authorized dashboard listings."""
    task_id: str
    name: str
    state: str
    version: int
    attempts: int
    created_at: str
    updated_at: str
    lease_owner: str | None
    lease_expires_at: str | None


@dataclass(frozen=True)
class TaskRecord:
    task_id: str
    name: str
    state: str
    steps: list[dict[str, Any]]
    result: dict[str, Any] | list[Any] | None
    error_code: str | None
    version: int
    attempts: int
    created_at: str
    updated_at: str
    lease_owner: str | None
    lease_expires_at: str | None


class TaskRuntime:
    """Task-oriented API with actor-scoped idempotent enqueue and versioned progress events."""

    def __init__(self, store: WorkflowRunStore, *, authorize: Callable[[str, str, str], bool] | None = None):
        if not isinstance(store, WorkflowRunStore):
            raise TypeError("store must be a WorkflowRunStore.")
        if authorize is not None and not callable(authorize):
            raise TypeError("authorize must be callable.")
        self.store = store
        self.authorize = authorize

    @staticmethod
    def _actor(actor_id: str) -> str:
        if not isinstance(actor_id, str) or not actor_id.strip() or len(actor_id) > 200 or "\x00" in actor_id:
            raise ValueError("actor_id must be non-empty bounded text.")
        return actor_id.strip()

    @staticmethod
    def _scoped_idempotency_key(actor: str, key: str) -> str:
        """Keep caller retry keys unique inside an authenticated actor namespace."""
        if not isinstance(key, str) or not key.strip() or len(key) > 200 or "\x00" in key:
            raise ValueError("idempotency_key must be non-empty bounded text.")
        return hashlib.sha256((actor + "\x00" + key.strip()).encode("utf-8")).hexdigest()

    def _allowed(self, actor: str, action: str, task_id: str) -> bool:
        if self.authorize is None:
            return False
        try:
            return self.authorize(actor, action, task_id) is True
        except Exception:
            return False

    @staticmethod
    def _record(run: WorkflowRun) -> TaskRecord:
        return TaskRecord(run.run_id, run.workflow_name, run.state, run.definition, run.result,
                          run.error_code, run.version, run.attempts, run.created_at, run.updated_at,
                          run.lease_owner, run.lease_expires_at)

    def enqueue(self, *, actor_id: str, idempotency_key: str, name: str, steps: list[dict[str, Any]]) -> TaskRecord:
        actor = self._actor(actor_id)
        if not self._allowed(actor, "task:create", ""):
            raise PermissionError("task creation is not authorized.")
        scoped_key = self._scoped_idempotency_key(actor, idempotency_key)
        run = self.store.create(idempotency_key=scoped_key, workflow_name=name, definition=steps)
        existing = self.store.list_task_events(run.run_id, limit=1)
        if not existing:
            self.store.append_task_event(run.run_id, kind="task.created", actor_id=actor,
                                         payload={"name": run.workflow_name, "step_count": len(run.definition)},
                                         expected_version=run.version)
        return self._record(self.store.get(run.run_id))

    def list_tasks(
        self, *, actor_id: str, state: str | None = None, limit: int = 50,
    ) -> list[TaskRecord]:
        """List recent tasks for an explicitly authorized supervisor/dashboard.

        Listing is a separate capability from reading a known task ID because it
        exposes task existence and metadata across the runtime. Results are newest
        first and bounded by the store to at most 100 records.
        """
        actor = self._actor(actor_id)
        if not self._allowed(actor, "task:list", ""):
            raise PermissionError("task listing is not authorized.")
        return [self._record(run) for run in self.store.list_runs(state=state, limit=limit)]

    def list_task_summaries(
        self, *, actor_id: str, state: str | None = None, limit: int = 50,
    ) -> list[TaskSummary]:
        """List redacted task metadata without exposing definitions or results.

        Requires the dedicated task:list capability. A caller must separately
        hold task:read for any task detail or event stream it subsequently opens.
        """
        actor = self._actor(actor_id)
        if not self._allowed(actor, "task:list", ""):
            raise PermissionError("task listing is not authorized.")
        runs = self.store.list_runs(state=state, limit=limit)
        return [
            TaskSummary(
                task_id=run.run_id,
                name=run.workflow_name,
                state=run.state,
                version=run.version,
                attempts=run.attempts,
                created_at=run.created_at,
                updated_at=run.updated_at,
                lease_owner=run.lease_owner,
                lease_expires_at=run.lease_expires_at,
            )
            for run in runs
        ]

    def get(self, *, actor_id: str, task_id: str) -> TaskRecord:
        actor = self._actor(actor_id)
        if not self._allowed(actor, "task:read", task_id):
            raise PermissionError("task read is not authorized.")
        run = self.store.get(task_id)
        if run is None:
            raise KeyError("task not found.")
        return self._record(run)

    def approve(self, *, actor_id: str, task_id: str, session_id: str, expected_version: int) -> TaskRecord:
        """Approve the exact queued workflow version for the authenticated session."""
        actor = self._actor(actor_id)
        if not self._allowed(actor, "task:approve", task_id):
            raise PermissionError("task approval is not authorized.")
        run = self.store.get(task_id)
        if run is None:
            raise KeyError("task not found.")
        approved = self.store.approve_with_event(
            task_id, actor_id=actor, session_id=session_id,
            expected_version=expected_version,
        )
        return self._record(approved)

    def revoke_session_approvals(self, session_id: str) -> list[TaskRecord]:
        """Cancel queued tasks whose approval was granted by the exact revoked session."""
        import hashlib
        if not isinstance(session_id, str) or not session_id.strip() or len(session_id) > 512 or chr(0) in session_id:
            raise ValueError("session_id must be non-empty bounded text.")
        session_hash = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
        revoked: list[TaskRecord] = []
        cursor: tuple[str, str] | None = None
        while True:
            page = self.store.list_queued_approved_by_session(session_hash, limit=100, after=cursor)
            if not page:
                break
            for run in page:
                cursor = (run.created_at, run.run_id)
                if not run.approved_by or not self._allowed(run.approved_by, "task:cancel", run.run_id):
                    continue
                try:
                    cancelled = self.store.transition_with_event(
                        run.run_id, expected_version=run.version,
                        to_state="cancelled", error_code="session_revoked",
                        event_kind="task.cancelled", actor_id=run.approved_by,
                        event_payload={"state_version": run.version + 1, "reason": "session_revoked"},
                    )
                except WorkflowConflict:
                    continue
                revoked.append(self._record(cancelled))
        return revoked
