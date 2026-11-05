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

_EVENT_KINDS = frozenset({"task.created", "task.claimed", "task.lease_renewed", "task.progress", "task.artifact", "task.completed", "task.failed", "task.cancelled", "task.interrupted", "task.step.started", "task.step.succeeded", "task.step.failed", "task.step.needs_reconciliation", "task.step.reconciled"})
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


class TaskLeaseRenewer:
    """Bounded callable adapter for WorkerShutdownCoordinator heartbeats.

    It reloads the current task version before each renewal so ordinary
    progress/artifact events do not leave a long-running worker with a stale
    compare-and-swap version. At most three CAS attempts are made per heartbeat.
    """

    MAX_CAS_ATTEMPTS = 3

    def __init__(
        self, runtime: "TaskRuntime", *, actor_id: str, task_id: str,
        worker_id: str, lease_seconds: int = 60,
    ) -> None:
        if not isinstance(runtime, TaskRuntime):
            raise TypeError("runtime must be a TaskRuntime.")
        self.runtime = runtime
        self.actor_id = TaskRuntime._actor(actor_id)
        if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 200 or "\x00" in task_id:
            raise ValueError("task_id must be non-empty bounded text.")
        if not isinstance(worker_id, str) or not worker_id.strip() or len(worker_id) > 200 or "\x00" in worker_id:
            raise ValueError("worker_id must be non-empty bounded text.")
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be an integer from 1 to 3600.")
        self.task_id = task_id.strip()
        self.worker_id = worker_id.strip()
        self.lease_seconds = lease_seconds

    def __call__(self) -> bool:
        if not self.runtime._allowed(self.actor_id, "task:lease:renew", self.task_id):
            raise PermissionError("task lease renewal is not authorized.")
        for attempt in range(self.MAX_CAS_ATTEMPTS):
            current = self.runtime.store.get(self.task_id)
            if current is None:
                raise KeyError("task not found.")
            if current.state != "running" or current.lease_owner != self.worker_id:
                raise WorkflowTransitionError("worker no longer owns a running task lease.")
            try:
                self.runtime.renew_lease(
                    actor_id=self.actor_id,
                    task_id=self.task_id,
                    worker_id=self.worker_id,
                    expected_version=current.version,
                    lease_seconds=self.lease_seconds,
                )
                return True
            except WorkflowConflict:
                # Retry only if a concurrent journal update advanced the version.
                # An unchanged version indicates expiry or another non-retryable
                # conflict; a changed owner/state is rejected on the next read.
                latest = self.runtime.store.get(self.task_id)
                if latest is None or latest.version == current.version or attempt + 1 == self.MAX_CAS_ATTEMPTS:
                    raise
        return False


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
        run = self.store.create(
            idempotency_key=idempotency_key, workflow_name=name, definition=steps,
            event_actor_id=actor,
        )
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

    def recover_expired(
        self, *, actor_id: str, task_id: str | None = None, limit: int = 50,
    ):
        """Recover one expired run or a bounded page; never replay workflow work."""
        actor = self._actor(actor_id)
        if task_id is not None:
            if not self._allowed(actor, "task:recover", task_id):
                raise PermissionError("task recovery is not authorized.")
            return self._record(self.store.recover_expired_run(task_id, actor_id=actor))

        if not self._allowed(actor, "task:recover", ""):
            raise PermissionError("task recovery is not authorized.")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer from 1 to 100.")
        recovered = []
        now = datetime.now(timezone.utc)
        for run in self.store.list_expired_runs(now=now.isoformat(), limit=limit):
            if not self._allowed(actor, "task:recover", run.run_id):
                continue
            recovered.append(self._record(
                self.store.recover_expired_run(run.run_id, actor_id=actor)
            ))
        return recovered

    def reconcile_step(
        self, *, actor_id: str, task_id: str, step_id: str,
        expected_step_version: int, decision: str, reason: str,
    ):
        """Record an audited operator disposition; this does not resume the task."""
        actor = self._actor(actor_id)
        if not self._allowed(actor, "task:step:resolve", task_id):
            raise PermissionError("workflow step reconciliation is not authorized.")
        return self.store.reconcile_step(
            task_id, step_id, actor_id=actor,
            expected_step_version=expected_step_version,
            decision=decision, reason=reason,
        )

    def list_steps(self, *, actor_id: str, task_id: str):
        """Read ordered, argument-redacted step metadata under task:read."""
        actor = self._actor(actor_id)
        if not self._allowed(actor, "task:read", task_id):
            raise PermissionError("task step read is not authorized.")
        if self.store.get(task_id) is None:
            raise KeyError("task not found.")
        return self.store.list_steps(task_id)

    def transition_step(
        self, *, actor_id: str, task_id: str, step_id: str, worker_id: str,
        expected_step_version: int, to_status: str,
    ):
        """Authorize and atomically journal one worker-owned step transition."""
        actor = self._actor(actor_id)
        permissions = {
            "running": "task:step:start",
            "succeeded": "task:step:finish",
            "failed": "task:step:finish",
            "needs_reconciliation": "task:step:reconcile",
        }
        if not isinstance(to_status, str):
            raise ValueError("Unknown workflow step status.")
        permission = permissions.get(to_status)
        if permission is None:
            raise ValueError("Unknown workflow step status.")
        if not self._allowed(actor, permission, task_id):
            raise PermissionError("workflow step transition is not authorized.")
        return self.store.transition_step(
            task_id, step_id, worker_id=worker_id, actor_id=actor,
            expected_step_version=expected_step_version, to_status=to_status,
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

    def renew_lease(
        self, *, actor_id: str, task_id: str, worker_id: str,
        expected_version: int, lease_seconds: int = 60,
    ) -> TaskRecord:
        """Renew the current worker lease with authorization and compare-and-swap."""
        actor = self._actor(actor_id)
        if not self._allowed(actor, "task:lease:renew", task_id):
            raise PermissionError("task lease renewal is not authorized.")
        if not isinstance(worker_id, str) or not worker_id.strip() or len(worker_id) > 200 or "\x00" in worker_id:
            raise ValueError("worker_id must be non-empty bounded text.")
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("expected_version must be a non-negative integer.")
        renewed = self.store.renew_lease(
            task_id, worker_id=worker_id.strip(),
            expected_version=expected_version, lease_seconds=lease_seconds,
            event_actor_id=actor,
        )
        return self._record(renewed)

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
        expiry = run.lease_expires_at
        if not expiry:
            raise WorkflowConflict("workflow lease expiry is missing.")
        try:
            expiry_dt = datetime.fromisoformat(expiry)
        except (TypeError, ValueError) as exc:
            raise WorkflowConflict("workflow lease expiry is malformed; completion is unsafe.") from exc
        if expiry_dt.tzinfo is None or expiry_dt.utcoffset() is None:
            raise WorkflowConflict("workflow lease expiry must include a timezone; completion is unsafe.")
        if expiry_dt <= datetime.now(timezone.utc):
            raise WorkflowConflict("expired lease cannot complete task.")
        completed = self.store.transition(
            task_id, expected_version=expected_version, to_state="succeeded",
            result=result, expected_lease_owner=worker_id,
            event_kind="task.completed", event_actor_id=actor,
            event_payload={"state_version": expected_version + 1},
        )
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
        expiry = run.lease_expires_at
        if not expiry:
            raise WorkflowConflict("workflow lease expiry is missing.")
        try:
            expiry_dt = datetime.fromisoformat(expiry)
        except (TypeError, ValueError) as exc:
            raise WorkflowConflict("workflow lease expiry is malformed; failure update is unsafe.") from exc
        if expiry_dt.tzinfo is None or expiry_dt.utcoffset() is None:
            raise WorkflowConflict("workflow lease expiry must include a timezone; failure update is unsafe.")
        if expiry_dt <= datetime.now(timezone.utc):
            raise WorkflowConflict("expired lease cannot fail task.")
        if not isinstance(error_code, str) or not _CODE.fullmatch(error_code):
            raise ValueError("error_code must be a bounded identifier.")
        failed = self.store.transition(
            task_id, expected_version=expected_version, to_state="failed",
            error_code=error_code, expected_lease_owner=worker_id,
            event_kind="task.failed", event_actor_id=actor,
            event_payload={"error_code": error_code, "state_version": expected_version + 1},
        )
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
            event_actor_id=actor,
            event_payload={"name": original.workflow_name, "step_count": len(original.definition),
                           "retry_of": original.run_id, "source_state": original.state,
                           "source_version": original.version,
                           "approval_required": self.store._requires_approval(original.definition)},
        )
        return self._record(self.store.get(retried.run_id))

    def _authorized_operation(self, actor: str, operation_id: str, action: str):
        operation = self.store.get_operation(operation_id)
        if operation is None or not self._allowed(actor, action, operation.task_id):
            # Do not expose whether an operation ID exists to unauthorized callers.
            raise PermissionError("operation access is not authorized.")
        return operation

    def prepare_operation(self, *, actor_id: str, task_id: str, idempotency_key: str,
                          action: str, request: dict[str, Any]):
        actor = self._actor(actor_id)
        if not self._allowed(actor, "task:operation:prepare", task_id):
            raise PermissionError("operation preparation is not authorized.")
        return self.store.prepare_operation(
            task_id, idempotency_key=idempotency_key, action=action, request=request,
        )

    def mark_operation_in_flight(self, *, actor_id: str, operation_id: str,
                                 worker_id: str, expected_version: int):
        actor = self._actor(actor_id)
        self._authorized_operation(actor, operation_id, "task:operation:dispatch")
        return self.store.mark_operation_in_flight(
            operation_id, worker_id=worker_id, expected_version=expected_version,
        )

    def reconcile_abandoned_operation(self, *, actor_id: str, operation_id: str,
                                      expected_version: int):
        actor = self._actor(actor_id)
        self._authorized_operation(actor, operation_id, "task:operation:recover")
        return self.store.reconcile_abandoned_operation(
            operation_id, expected_version=expected_version,
        )

    def record_operation_outcome(self, *, actor_id: str, operation_id: str,
                                 expected_version: int, status: str,
                                 provider_reference: str | None = None,
                                 outcome: dict[str, Any] | list[Any] | None = None):
        actor = self._actor(actor_id)
        self._authorized_operation(actor, operation_id, "task:operation:resolve")
        return self.store.record_operation_outcome(
            operation_id, expected_version=expected_version, status=status,
            provider_reference=provider_reference, outcome=outcome,
        )

    def cancel(self, *, actor_id: str, task_id: str, expected_version: int) -> TaskRecord:
        actor = self._actor(actor_id)
        if not self._allowed(actor, "task:cancel", task_id):
            raise PermissionError("task cancellation is not authorized.")
        run = self.store.get(task_id)
        if run is None:
            raise KeyError("task not found.")
        cancelled = self.store.transition(
            task_id, expected_version=expected_version, to_state="cancelled",
            error_code="cancelled", event_kind="task.cancelled",
            event_actor_id=actor, event_payload={"state_version": expected_version + 1},
        )
        return self._record(self.store.get(task_id))
