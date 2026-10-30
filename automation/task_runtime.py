"""Unified task facade over Yuki's durable workflow journal.

This service records task state and progress; it does not execute arbitrary
code or automatically replay work after an expired worker lease.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any, Callable

from automation.workflow_store import WorkflowConflict, WorkflowRun, WorkflowRunStore, WorkflowTransitionError

_EVENT_KINDS = frozenset({"task.created", "task.progress", "task.artifact", "task.completed", "task.failed", "task.cancelled", "task.interrupted"})
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
    """Task-oriented API with idempotent enqueue and versioned progress events."""

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
        run = self.store.create(idempotency_key=idempotency_key, workflow_name=name, definition=steps)
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

    def events(
        self, *, actor_id: str, task_id: str, after_sequence: int = 0, limit: int = 100,
    ) -> list[TaskEvent]:
        """Read a bounded page of the authorized task event stream.

        Clients can poll incrementally by passing the last observed event sequence
        as after_sequence. Event payloads remain behind task:read authorization.
        """
        actor = self._actor(actor_id)
        if not self._allowed(actor, "task:read", task_id):
            raise PermissionError("task event read is not authorized.")
        return self.store.list_task_events(
            task_id, after_sequence=after_sequence, limit=limit,
        )

    def record_progress(self, *, actor_id: str, task_id: str, expected_version: int,
                        message: str, percent: int | None = None) -> TaskRecord:
        actor = self._actor(actor_id)
        if not self._allowed(actor, "task:progress", task_id):
            raise PermissionError("task progress update is not authorized.")
        if not isinstance(message, str) or not message.strip() or len(message) > 1000 or "\x00" in message:
            raise ValueError("message must be non-empty bounded text.")
        if percent is not None and (type(percent) is not int or not 0 <= percent <= 100):
            raise ValueError("percent must be an integer from 0 to 100.")
        run = self.store.get(task_id)
        if run is None:
            raise KeyError("task not found.")
        if run.state != "running":
            raise WorkflowTransitionError("progress can only be recorded for a running task.")
        self.store.append_task_event(task_id, kind="task.progress", actor_id=actor,
                                     payload={"message": message.strip(), "percent": percent},
                                     expected_version=expected_version)
        return self._record(self.store.get(task_id))

    def record_artifact(self, *, actor_id: str, task_id: str, expected_version: int,
                        artifact_type: str, reference: str, metadata: dict[str, Any] | None = None) -> TaskRecord:
        actor = self._actor(actor_id)
        if not self._allowed(actor, "task:artifact", task_id):
            raise PermissionError("task artifact update is not authorized.")
        if not isinstance(artifact_type, str) or not _CODE.fullmatch(artifact_type):
            raise ValueError("artifact_type must be a bounded identifier.")
        if not isinstance(reference, str) or not reference.strip() or len(reference) > 2000 or "\x00" in reference:
            raise ValueError("reference must be non-empty bounded text.")
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, dict):
            raise ValueError("metadata must be an object.")
        run = self.store.get(task_id)
        if run is None:
            raise KeyError("task not found.")
        if run.state != "running":
            raise WorkflowTransitionError("artifacts can only be attached to a running task.")
        self.store.append_task_event(task_id, kind="task.artifact", actor_id=actor,
                                     payload={"artifact_type": artifact_type, "reference": reference.strip(),
                                              "metadata": metadata},
                                     expected_version=expected_version)
        return self._record(self.store.get(task_id))

    def complete(self, *, actor_id: str, task_id: str, expected_version: int,
                 worker_id: str, result: dict[str, Any] | list[Any]) -> TaskRecord:
        actor = self._actor(actor_id)
        if not self._allowed(actor, "task:complete", task_id):
            raise PermissionError("task completion is not authorized.")
        run = self.store.get(task_id)
        if run is None:
            raise KeyError("task not found.")
        if not isinstance(worker_id, str) or run.state != "running" or run.lease_owner != worker_id:
            raise WorkflowTransitionError("only the current worker may complete a running task.")
        if not run.lease_expires_at or datetime.fromisoformat(run.lease_expires_at) < datetime.now(timezone.utc):
            raise WorkflowConflict("expired lease cannot complete task.")
        completed = self.store.transition(task_id, expected_version=expected_version,
                                          to_state="succeeded", result=result)
        self.store.append_task_event(task_id, kind="task.completed", actor_id=actor,
                                     payload={"state_version": completed.version},
                                     expected_version=completed.version)
        return self._record(self.store.get(task_id))

    def fail(self, *, actor_id: str, task_id: str, expected_version: int,
             worker_id: str, error_code: str) -> TaskRecord:
        actor = self._actor(actor_id)
        if not self._allowed(actor, "task:fail", task_id):
            raise PermissionError("task failure update is not authorized.")
        run = self.store.get(task_id)
        if run is None:
            raise KeyError("task not found.")
        if run.state != "running" or run.lease_owner != worker_id:
            raise WorkflowTransitionError("only the current worker may fail a running task.")
        if not isinstance(error_code, str) or not _CODE.fullmatch(error_code):
            raise ValueError("error_code must be a bounded identifier.")
        failed = self.store.transition(task_id, expected_version=expected_version,
                                       to_state="failed", error_code=error_code)
        self.store.append_task_event(task_id, kind="task.failed", actor_id=actor,
                                     payload={"error_code": error_code, "state_version": failed.version},
                                     expected_version=failed.version)
        return self._record(self.store.get(task_id))



    def retry_failed(self, *, actor_id: str, task_id: str, expected_version: int) -> TaskRecord:
        """Explicitly enqueue a fresh task from failed/interrupted work; never replay in place.

        The caller must be separately authorized for task:retry. The new run receives
        a distinct idempotency key and fresh approval state, so approval-required work
        must be approved again before a worker can claim it.
        """
        actor = self._actor(actor_id)
        if not self._allowed(actor, "task:retry", task_id):
            raise PermissionError("task retry is not authorized.")
        if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 200:
            raise ValueError("task_id must be non-empty bounded text.")
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("expected_version must be a non-negative integer.")
        original = self.store.get(task_id)
        if original is None:
            raise KeyError("task not found.")
        if original.version != expected_version:
            raise WorkflowConflict("task version changed; reload before retrying.")
        if original.state not in {"failed", "interrupted"}:
            raise WorkflowTransitionError("only failed or interrupted tasks can be retried.")
        # A stable key makes duplicate submissions for the same observed version idempotent.
        retry_key = f"retry:{original.run_id}:{expected_version}"
        retried = self.store.create(
            idempotency_key=retry_key,
            workflow_name=original.workflow_name,
            definition=original.definition,
        )
        existing_events = self.store.list_task_events(retried.run_id, limit=1)
        if not existing_events:
            self.store.append_task_event(
                retried.run_id, kind="task.created", actor_id=actor,
                payload={"name": retried.workflow_name, "step_count": len(retried.definition),
                         "retry_of": original.run_id, "source_state": original.state,
                         "source_version": original.version,
                         "approval_required": self.store._requires_approval(original.definition)},
                expected_version=retried.version,
            )
        return self._record(self.store.get(retried.run_id))

    def recover_expired(self, *, actor_id: str, limit: int = 50) -> list[TaskRecord]:
        """Mark expired worker leases interrupted and journal each recovery; never replay them."""
        actor = self._actor(actor_id)
        if not self._allowed(actor, "task:recover", ""):
            raise PermissionError("task recovery is not authorized.")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer from 1 to 100.")
        interrupted = self.store.mark_expired_running_as_interrupted(limit=limit)
        records: list[TaskRecord] = []
        for run in interrupted:
            self.store.append_task_event(
                run.run_id, kind="task.interrupted", actor_id=actor,
                payload={"state": "interrupted", "error_code": "lease_expired",
                         "automatic_replay": False},
                expected_version=run.version,
            )
            records.append(self._record(self.store.get(run.run_id)))
        return records

    def cancel(self, *, actor_id: str, task_id: str, expected_version: int) -> TaskRecord:
        actor = self._actor(actor_id)
        if not self._allowed(actor, "task:cancel", task_id):
            raise PermissionError("task cancellation is not authorized.")
        run = self.store.get(task_id)
        if run is None:
            raise KeyError("task not found.")
        cancelled = self.store.transition(task_id, expected_version=expected_version,
                                          to_state="cancelled", error_code="cancelled")
        self.store.append_task_event(task_id, kind="task.cancelled", actor_id=actor,
                                     payload={"state_version": cancelled.version},
                                     expected_version=cancelled.version)
        return self._record(self.store.get(task_id))
