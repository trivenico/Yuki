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

_EVENT_KINDS = frozenset({"task.created", "task.approved", "task.progress", "task.artifact", "task.completed", "task.failed", "task.cancelled", "task.interrupted", "task.blocked", "task.sandbox.started", "task.sandbox.completed", "task.sandbox.cancelled", "task.sandbox.failed"})
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
    agent_execution_spec_id: str | None = None
    dependencies: tuple[str, ...] = ()
    owner_id: str | None = None


class TaskRuntime:
    """Task-oriented API with idempotent enqueue and versioned progress events."""

    def __init__(self, store: WorkflowRunStore, *, authorize: Callable[[str, str, str], bool] | None = None, artifact_store=None):
        if not isinstance(store, WorkflowRunStore):
            raise TypeError("store must be a WorkflowRunStore.")
        if authorize is not None and not callable(authorize):
            raise TypeError("authorize must be callable.")
        if artifact_store is not None and not callable(getattr(artifact_store, "get", None)):
            raise TypeError("artifact_store must implement get().")
        self.store = store
        self.authorize = authorize
        self.artifact_store = artifact_store

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

    def assert_worker_service_permissions(self, actor_id: str) -> None:
        """Fail startup unless a worker identity has its core execution grants.

        Per-task ownership override is intentionally checked against each real
        task by _authorized_run; it is not treated as a global startup grant.
        """
        actor = self._actor(actor_id)
        required = ("task:lease:renew", "task:complete", "task:fail")
        missing = tuple(action for action in required if not self._allowed(actor, action, ""))
        if missing:
            raise PermissionError(
                "worker service actor is missing required task capabilities: "
                + ", ".join(missing)
            )

    def assert_recovery_permissions(self, actor_id: str) -> None:
        """Fail startup when a supervisor cannot perform its configured recovery duty."""
        actor = self._actor(actor_id)
        if not self._allowed(actor, "task:recover", ""):
            raise PermissionError(
                "worker service actor is missing required task capability: task:recover"
            )

    def is_authorized(self, actor_id: str, action: str, task_id: str) -> bool:
        """Expose a fail-closed authorization decision to trusted execution adapters."""
        try:
            actor = self._actor(actor_id)
            if not isinstance(action, str) or not action.strip() or len(action) > 120 or "\x00" in action:
                return False
            if not isinstance(task_id, str) or len(task_id) > 200 or "\x00" in task_id:
                return False
            if not self._allowed(actor, action.strip(), task_id):
                return False
            if self._allowed(actor, "task:owner:override", task_id):
                return self.store.get(task_id) is not None
            return self.store.get_owned(task_id, owner_id=actor) is not None
        except (TypeError, ValueError):
            return False

    def _authorized_run(self, actor: str, task_id: str, action: str) -> WorkflowRun:
        if not self._allowed(actor, action, task_id):
            raise PermissionError("task access is not authorized.")
        if self._allowed(actor, "task:owner:override", task_id):
            run = self.store.get(task_id)
        else:
            run = self.store.get_owned(task_id, owner_id=actor)
        if run is None:
            raise KeyError("task not found.")
        return run

    def _record(self, run: WorkflowRun) -> TaskRecord:
        return TaskRecord(run.run_id, run.workflow_name, run.state, run.definition, run.result,
                          run.error_code, run.version, run.attempts, run.created_at, run.updated_at,
                          run.lease_owner, run.lease_expires_at, run.agent_execution_spec_id,
                          self.store.get_dependencies(run.run_id), run.owner_id)

    def register_agent_spec(self, *, actor_id: str, spec: Any) -> dict[str, Any]:
        """Register immutable agent configuration behind a dedicated authorization."""
        actor = self._actor(actor_id)
        if not self._allowed(actor, "task:spec:register", ""):
            raise PermissionError("agent specification registration is not authorized.")
        from automation.agent_execution_spec import AgentExecutionSpec
        if not isinstance(spec, AgentExecutionSpec):
            raise TypeError("spec must be an AgentExecutionSpec.")
        return self.store.register_agent_execution_spec(spec, owner_id=actor)

    def register_agent_specs(self, *, actor_id: str, specs: tuple[Any, ...]) -> tuple[dict[str, Any], ...]:
        """Authorize and atomically register a bounded, validated agent dependency graph."""
        actor = self._actor(actor_id)
        if not self._allowed(actor, "task:spec:register", ""):
            raise PermissionError("agent specification registration is not authorized.")
        from automation.agent_execution_spec import AgentExecutionSpec
        if not isinstance(specs, tuple) or any(
            not isinstance(spec, AgentExecutionSpec) for spec in specs
        ):
            raise TypeError("specs must be a tuple of AgentExecutionSpec values.")
        return self.store.register_agent_execution_specs(specs, owner_id=actor)

    def get_agent_spec(self, *, actor_id: str, spec_id: str) -> Any:
        """Read one persisted agent spec; listing and registration are separate capabilities."""
        actor = self._actor(actor_id)
        if not self._allowed(actor, "task:spec:read", spec_id):
            raise PermissionError("agent specification read is not authorized.")
        value = self.store.get_agent_execution_spec(spec_id, owner_id=actor)
        if value is None:
            raise KeyError("agent specification not found.")
        from automation.agent_execution_spec import AgentExecutionSpec
        return AgentExecutionSpec.from_dict(value)

    def list_agent_specs(self, *, actor_id: str, limit: int = 50) -> list[Any]:
        actor = self._actor(actor_id)
        if not self._allowed(actor, "task:spec:list", ""):
            raise PermissionError("agent specification listing is not authorized.")
        from automation.agent_execution_spec import AgentExecutionSpec
        return [
            AgentExecutionSpec.from_dict(value)
            for value in self.store.list_agent_execution_specs(owner_id=actor, limit=limit)
        ]

    def enqueue(
        self, *, actor_id: str, idempotency_key: str, name: str,
        steps: list[dict[str, Any]], agent_execution_spec_id: str | None = None,
        dependencies: tuple[str, ...] = (),
    ) -> TaskRecord:
        actor = self._actor(actor_id)
        if not self._allowed(actor, "task:create", ""):
            raise PermissionError("task creation is not authorized.")
        if not isinstance(dependencies, tuple) or len(dependencies) > 32:
            raise ValueError("dependencies must be a tuple containing at most 32 task IDs.")
        for dependency_id in dependencies:
            if not isinstance(dependency_id, str) or not self._allowed(
                actor, "task:dependency", dependency_id
            ):
                raise PermissionError("task dependency access is not authorized.")
        run = self.store.create(
            idempotency_key=idempotency_key, workflow_name=name, definition=steps,
            agent_execution_spec_id=agent_execution_spec_id,
            dependencies=dependencies,
            owner_id=actor,
            initial_event={
                "kind": "task.created",
                "actor_id": actor,
                "payload": {
                    "name": name, "step_count": len(steps),
                    "dependencies": list(dependencies),
                },
            },
        )
        return self._record(run)

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
        if self._allowed(actor, "task:owner:list:any", ""):
            runs = self.store.list_runs(state=state, limit=limit)
        else:
            runs = self.store.list_owned_runs(owner_id=actor, state=state, limit=limit)
        return [self._record(run) for run in runs]

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
        if self._allowed(actor, "task:owner:list:any", ""):
            runs = self.store.list_runs(state=state, limit=limit)
        else:
            runs = self.store.list_owned_runs(owner_id=actor, state=state, limit=limit)
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
        return self._record(self._authorized_run(actor, task_id, "task:read"))

    def get_artifact(self, *, actor_id: str, task_id: str, reference: str) -> bytes:
        """Retrieve bytes only when the reference is journaled on this authorized task."""
        if self.artifact_store is None:
            raise RuntimeError("task artifact storage is not configured.")
        actor = self._actor(actor_id)
        self._authorized_run(actor, task_id, "task:artifact")
        if not isinstance(reference, str) or not reference.strip() or len(reference) > 2000:
            raise ValueError("reference must be bounded non-empty text.")
        events = self.store.list_task_events(task_id, after_sequence=0, limit=500)
        journaled = any(
            event.kind == "task.artifact"
            and event.payload.get("reference") == reference
            for event in events
        )
        if not journaled:
            raise KeyError("artifact reference is not registered to this task.")
        run = self.store.get(task_id)
        if run is None:
            raise KeyError("task not found.")
        return self.artifact_store.get(
            actor_id=actor, task=self._record(run), reference=reference,
        )

    def approve(self, *, actor_id: str, task_id: str, session_id: str, expected_version: int) -> TaskRecord:
        """Approve the exact queued workflow version for the authenticated session."""
        actor = self._actor(actor_id)
        run = self._authorized_run(actor, task_id, "task:approve")
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
        # Page by stable creation cursor, not a fixed recent-task window. Only
        # queued tasks approved by this exact session are ever considered.
        cursor: tuple[str, str] | None = None
        while True:
            page = self.store.list_queued_approved_by_session(session_hash, limit=100, after=cursor)
            if not page:
                break
            for run in page:
                cursor = (run.created_at, run.run_id)
                if not run.approved_by or run.owner_id != run.approved_by:
                    continue
                if not self._allowed(run.approved_by, "task:cancel", run.run_id):
                    continue
                try:
                    cancelled = self.store.transition_with_event(
                        run.run_id, expected_version=run.version,
                        to_state="cancelled", error_code="session_revoked",
                        event_kind="task.cancelled", actor_id=run.approved_by,
                        event_payload={"state_version": run.version + 2, "reason": "session_revoked"},
                    )
                except WorkflowConflict:
                    # A concurrent worker/state change won the race; never
                    # overwrite it with a stale logout cancellation.
                    continue
                revoked.append(self._record(cancelled))
        return revoked

    def events(
        self, *, actor_id: str, task_id: str, after_sequence: int = 0, limit: int = 100,
    ) -> list[TaskEvent]:
        """Read a bounded page of the authorized task event stream.

        Clients can poll incrementally by passing the last observed event sequence
        as after_sequence. Event payloads remain behind task:read authorization.
        """
        actor = self._actor(actor_id)
        self._authorized_run(actor, task_id, "task:read")
        return self.store.list_task_events(
            task_id, after_sequence=after_sequence, limit=limit,
        )

    def record_progress(self, *, actor_id: str, task_id: str, expected_version: int,
                        message: str, percent: int | None = None,
                        worker_session: tuple[str, str] | None = None) -> TaskRecord:
        actor = self._actor(actor_id)
        run = self._authorized_run(actor, task_id, "task:progress")
        if not isinstance(message, str) or not message.strip() or len(message) > 1000 or "\x00" in message:
            raise ValueError("message must be non-empty bounded text.")
        if percent is not None and (type(percent) is not int or not 0 <= percent <= 100):
            raise ValueError("percent must be an integer from 0 to 100.")
        if run.state != "running" or not run.lease_owner:
            raise WorkflowTransitionError("progress requires a running task with an active worker lease.")
        self.store.append_task_event(
            task_id, kind="task.progress", actor_id=actor,
            payload={"message": message.strip(), "percent": percent},
            expected_version=expected_version, expected_lease_owner=run.lease_owner,
            expected_worker_session=worker_session,
        )
        return self._record(self.store.get(task_id))

    def record_sandbox_event(
        self, *, actor_id: str, task_id: str, worker_id: str,
        expected_version: int, kind: str, profile_id: str,
        image_digest: str, container_id: str,
        exit_code: int | None = None, duration_ms: int | None = None,
        output_bytes: int | None = None, timed_out: bool = False,
        cancelled: bool = False,
        worker_session: tuple[str, str] | None = None,
    ) -> TaskRecord:
        """Append allowlisted sandbox lifecycle metadata under the exact active task lease."""
        actor = self._actor(actor_id)
        run = self._authorized_run(actor, task_id, "task:sandbox:audit")
        if run.state != "running" or run.lease_owner != worker_id:
            raise WorkflowTransitionError("only the current worker may record sandbox lifecycle events.")
        if kind not in {
            "task.sandbox.started", "task.sandbox.completed",
            "task.sandbox.cancelled", "task.sandbox.failed",
        }:
            raise ValueError("unsupported sandbox lifecycle event.")
        if not isinstance(worker_id, str) or not worker_id.strip() or len(worker_id) > 200 or "\x00" in worker_id:
            raise ValueError("worker_id must be non-empty bounded text.")
        if not isinstance(profile_id, str) or not _CODE.fullmatch(profile_id):
            raise ValueError("profile_id must be a bounded identifier.")
        if not isinstance(image_digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_digest):
            raise ValueError("image_digest must be a pinned SHA-256 digest.")
        if not isinstance(container_id, str) or not re.fullmatch(r"yuki-[0-9a-f]{32}", container_id):
            raise ValueError("container_id must be a Yuki-generated container name.")
        for name, value in (("duration_ms", duration_ms), ("output_bytes", output_bytes)):
            if value is not None and (type(value) is not int or value < 0 or value > 2**53 - 1):
                raise ValueError(f"{name} must be a bounded non-negative integer.")
        if exit_code is not None and (type(exit_code) is not int or not -255 <= exit_code <= 255):
            raise ValueError("exit_code must be an integer from -255 to 255.")
        if type(timed_out) is not bool or type(cancelled) is not bool:
            raise ValueError("sandbox outcome flags must be booleans.")
        if kind == "task.sandbox.started" and any(
            value is not None for value in (exit_code, duration_ms, output_bytes)
        ):
            raise ValueError("sandbox start events cannot contain outcome metrics.")
        payload = {
            "profile_id": profile_id, "image_digest": image_digest,
            "container_id": container_id, "worker_id": worker_id.strip(),
            "exit_code": exit_code, "duration_ms": duration_ms,
            "output_bytes": output_bytes, "timed_out": timed_out,
            "cancelled": cancelled,
        }
        self.store.append_task_event(
            task_id, kind=kind, actor_id=actor, payload=payload,
            expected_version=expected_version, expected_lease_owner=worker_id.strip(),
            expected_worker_session=worker_session,
        )
        return self._record(self.store.get(task_id))

    def renew_lease(
        self, *, actor_id: str, task_id: str, worker_id: str,
        expected_version: int, lease_seconds: int = 60,
        worker_session: tuple[str, str] | None = None,
    ) -> TaskRecord:
        """Renew the current worker lease with authorization and compare-and-swap."""
        actor = self._actor(actor_id)
        self._authorized_run(actor, task_id, "task:lease:renew")
        if not isinstance(worker_id, str) or not worker_id.strip() or len(worker_id) > 200 or "\x00" in worker_id:
            raise ValueError("worker_id must be non-empty bounded text.")
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("expected_version must be a non-negative integer.")
        renewed = self.store.renew_lease(
            task_id, worker_id=worker_id.strip(),
            expected_version=expected_version, lease_seconds=lease_seconds,
            expected_worker_session=worker_session,
        )
        return self._record(renewed)

    def record_artifact(self, *, actor_id: str, task_id: str, expected_version: int,
                        artifact_type: str, reference: str, metadata: dict[str, Any] | None = None,
                        worker_session: tuple[str, str] | None = None) -> TaskRecord:
        actor = self._actor(actor_id)
        run = self._authorized_run(actor, task_id, "task:artifact")
        if not isinstance(artifact_type, str) or not _CODE.fullmatch(artifact_type):
            raise ValueError("artifact_type must be a bounded identifier.")
        if not isinstance(reference, str) or not reference.strip() or len(reference) > 2000 or "\x00" in reference:
            raise ValueError("reference must be non-empty bounded text.")
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, dict):
            raise ValueError("metadata must be an object.")
        if run.state != "running" or not run.lease_owner:
            raise WorkflowTransitionError("artifacts require a running task with an active worker lease.")
        self.store.append_task_event(
            task_id, kind="task.artifact", actor_id=actor,
            payload={"artifact_type": artifact_type, "reference": reference.strip(),
                     "metadata": metadata},
            expected_version=expected_version, expected_lease_owner=run.lease_owner,
            expected_worker_session=worker_session,
        )
        return self._record(self.store.get(task_id))

    def complete(self, *, actor_id: str, task_id: str, expected_version: int,
                 worker_id: str, result: dict[str, Any] | list[Any],
                 worker_session: tuple[str, str] | None = None) -> TaskRecord:
        actor = self._actor(actor_id)
        run = self._authorized_run(actor, task_id, "task:complete")
        if not isinstance(worker_id, str) or run.state != "running" or run.lease_owner != worker_id:
            raise WorkflowTransitionError("only the current worker may complete a running task.")
        if not run.lease_expires_at or datetime.fromisoformat(run.lease_expires_at) < datetime.now(timezone.utc):
            raise WorkflowConflict("expired lease cannot complete task.")
        completed = self.store.transition_with_event(
            task_id, expected_version=expected_version, to_state="succeeded",
            result=result, event_kind="task.completed", actor_id=actor,
            event_payload={"state_version": expected_version + 2},
            expected_lease_owner=worker_id,
            expected_worker_session=worker_session,
        )
        return self._record(completed)

    def fail(self, *, actor_id: str, task_id: str, expected_version: int,
             worker_id: str, error_code: str,
             worker_session: tuple[str, str] | None = None) -> TaskRecord:
        actor = self._actor(actor_id)
        run = self._authorized_run(actor, task_id, "task:fail")
        if run.state != "running" or run.lease_owner != worker_id:
            raise WorkflowTransitionError("only the current worker may fail a running task.")
        if not isinstance(error_code, str) or not _CODE.fullmatch(error_code):
            raise ValueError("error_code must be a bounded identifier.")
        failed = self.store.transition_with_event(
            task_id, expected_version=expected_version, to_state="failed",
            error_code=error_code, event_kind="task.failed", actor_id=actor,
            event_payload={"error_code": error_code, "state_version": expected_version + 2},
            expected_lease_owner=worker_id,
            expected_worker_session=worker_session,
        )
        return self._record(failed)



    def retry_failed(self, *, actor_id: str, task_id: str, expected_version: int) -> TaskRecord:
        """Explicitly enqueue a fresh task from failed/interrupted work; never replay in place.

        The caller must be separately authorized for task:retry. The new run receives
        a distinct idempotency key and fresh approval state, so approval-required work
        must be approved again before a worker can claim it.
        """
        actor = self._actor(actor_id)
        run = self._authorized_run(actor, task_id, "task:retry")
        if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 200:
            raise ValueError("task_id must be non-empty bounded text.")
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("expected_version must be a non-negative integer.")
        original = run
        if original.version != expected_version:
            raise WorkflowConflict("task version changed; reload before retrying.")
        if original.state not in {"failed", "interrupted"}:
            raise WorkflowTransitionError("only failed or interrupted tasks can be retried.")
        # Preserve prerequisite edges across retries; a retry must not shed
        # the original task's dependency constraints.
        dependencies = self.store.get_dependencies(original.run_id)
        for dependency_id in dependencies:
            if not self._allowed(actor, "task:dependency", dependency_id):
                raise PermissionError("task dependency access is not authorized for retry.")
        # Preserve the effective approval requirement in the retry audit record,
        # including the immutable agent specification's policy.
        spec_requires_approval = False
        if original.agent_execution_spec_id is not None:
            spec_record = self.store.get_agent_execution_spec_record(
                original.agent_execution_spec_id, owner_id=original.owner_id,
            )
            if spec_record is None:
                raise WorkflowConflict("retry agent specification is missing or not owned by the task account.")
            spec_policy = spec_record[0].get("approval_policy", "required")
            spec_requires_approval = spec_policy not in {
                "required", "per_action", "not_requested",
            } or spec_policy in {"required", "per_action"}
        approval_required = (
            self.store._requires_approval(original.definition) or spec_requires_approval
        )
        # A stable key makes duplicate submissions for the same observed version idempotent.
        retry_key = f"retry:{original.run_id}:{expected_version}"
        retried = self.store.create(
            idempotency_key=retry_key,
            workflow_name=original.workflow_name,
            definition=original.definition,
            agent_execution_spec_id=original.agent_execution_spec_id,
            dependencies=dependencies,
            retry_of_task_id=original.run_id,
            owner_id=original.owner_id,
            initial_event={
                "kind": "task.created",
                "actor_id": actor,
                "payload": {
                    "name": original.workflow_name,
                    "step_count": len(original.definition),
                    "retry_of": original.run_id,
                    "source_state": original.state,
                    "source_version": original.version,
                    "approval_required": approval_required,
                },
            },
        )
        return self._record(self.store.get(retried.run_id))

    def recover_expired(self, *, actor_id: str, limit: int = 50) -> list[TaskRecord]:
        """Mark expired worker leases interrupted and journal each recovery; never replay them."""
        actor = self._actor(actor_id)
        if not self._allowed(actor, "task:recover", ""):
            raise PermissionError("task recovery is not authorized.")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer from 1 to 100.")
        interrupted = self.store.recover_expired_with_events(actor_id=actor, limit=limit)
        return [self._record(run) for run in interrupted]

    def cancel(self, *, actor_id: str, task_id: str, expected_version: int) -> TaskRecord:
        actor = self._actor(actor_id)
        run = self._authorized_run(actor, task_id, "task:cancel")
        cancelled = self.store.transition_with_event(
            task_id, expected_version=expected_version, to_state="cancelled",
            error_code="cancelled", event_kind="task.cancelled", actor_id=actor,
            event_payload={"state_version": expected_version + 1},
        )
        return self._record(cancelled)
