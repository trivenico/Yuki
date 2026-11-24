"""Unified task facade over Yuki's durable workflow journal.

This service records task state and progress; it does not execute arbitrary
code or automatically replay work after an expired worker lease.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
import time
from typing import Any, Callable

from automation.workflow_store import WorkflowConflict, WorkflowRun, WorkflowRunStore, WorkflowTransitionError
from core.workload_identity import WorkloadCredentialVerifier

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
    """Task-oriented API with idempotent enqueue and versioned progress events."""

    def __init__(self, store: WorkflowRunStore, *, authorize: Callable[[str, str, str], bool] | None = None,
                 workload_credential_verifier: WorkloadCredentialVerifier | None = None,
                 workload_audience: str | None = None, credential_clock: Callable[[], float] = time.time):
        if not isinstance(store, WorkflowRunStore):
            raise TypeError("store must be a WorkflowRunStore.")
        if authorize is not None and not callable(authorize):
            raise TypeError("authorize must be callable.")
        if workload_credential_verifier is not None and not callable(getattr(workload_credential_verifier, "verify", None)):
            raise TypeError("workload_credential_verifier must implement verify().")
        if workload_credential_verifier is not None and (
            not isinstance(workload_audience, str) or not workload_audience.strip()
        ):
            raise ValueError("workload_audience is required with a credential verifier.")
        if not callable(credential_clock):
            raise TypeError("credential_clock must be callable.")
        self.store = store
        self.authorize = authorize
        self.workload_credential_verifier = workload_credential_verifier
        self.workload_audience = workload_audience
        self.credential_clock = credential_clock

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
    def _require_active_lease(run: WorkflowRun, worker_id: str, operation: str) -> str:
        if not isinstance(worker_id, str) or not worker_id.strip() or len(worker_id) > 200 or "\x00" in worker_id:
            raise ValueError("worker_id must be non-empty bounded text.")
        worker = worker_id.strip()
        if run.state != "running" or run.lease_owner != worker:
            raise WorkflowTransitionError(f"only the current worker may {operation} a running task.")
        if not run.lease_expires_at:
            raise WorkflowConflict("task has no active worker lease.")
        try:
            expiry = datetime.fromisoformat(run.lease_expires_at)
        except (TypeError, ValueError) as exc:
            raise WorkflowConflict("task lease expiry is invalid.") from exc
        if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
            raise WorkflowConflict("expired or invalid lease cannot modify task.")
        return worker

    def _verify_worker_credential(self, run: WorkflowRun, worker_id: str,
                                  credential: str | None, action: str) -> str | None:
        """Verify one-use workload proof and return the fenced lease generation.

        Legacy mode is retained only for migration. Production composition must
        configure a verifier and audience so every worker mutation requires a credential.
        """
        if self.workload_credential_verifier is None:
            if credential is not None:
                raise PermissionError("workload credential verifier is not configured.")
            return None
        if not isinstance(credential, str) or not credential:
            raise PermissionError("workload credential is required.")
        if not run.lease_generation:
            raise PermissionError("task lease has no cryptographic generation.")
        try:
            now = self.credential_clock()
            if type(now) not in (int, float) or now < 0:
                raise ValueError("invalid credential clock")
            claims = self.workload_credential_verifier.verify(
                credential, expected_audience=self.workload_audience,
                expected_task_id=run.run_id,
                expected_lease_generation=run.lease_generation, now=int(now),
            )
        except Exception as exc:
            raise PermissionError("workload credential rejected.") from exc
        if (
            claims.subject != worker_id
            or claims.task_id != run.run_id
            or claims.lease_generation != run.lease_generation
            or claims.audience != self.workload_audience
            or action not in claims.scopes
        ):
            raise PermissionError("workload credential is not authorized for this task action.")
        return run.lease_generation

    def active_lease(self, task_id: str):
        """Expose only a current lease snapshot for the trusted credential issuer."""
        from core.workload_credential_issuer import ActiveTaskLease
        run = self.store.get(task_id)
        if run is None or run.state != "running" or not run.lease_owner or not run.lease_generation:
            return None
        try:
            expiry = datetime.fromisoformat(run.lease_expires_at or "")
        except (TypeError, ValueError):
            return None
        if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
            return None
        return ActiveTaskLease(run.run_id, run.lease_owner, run.lease_generation, int(expiry.timestamp()))

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
        # Page by stable creation cursor, not a fixed recent-task window. Only
        # queued tasks approved by this exact session are ever considered.
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
        if not self._allowed(actor, "task:read", task_id):
            raise PermissionError("task event read is not authorized.")
        return self.store.list_task_events(
            task_id, after_sequence=after_sequence, limit=limit,
        )

    def record_progress(self, *, actor_id: str, task_id: str, worker_id: str,
                        expected_version: int, message: str,
                        percent: int | None = None,
                        workload_credential: str | None = None) -> TaskRecord:
        """Record progress only for the worker holding the current unexpired lease.

        worker_id is still an asserted identifier, not cryptographic workload
        authentication. Deployments must authenticate worker credentials before
        passing this value into the runtime.
        """
        actor = self._actor(actor_id)
        if not self._allowed(actor, "task:progress", task_id):
            raise PermissionError("task progress update is not authorized.")
        if not isinstance(worker_id, str) or not worker_id.strip() or len(worker_id) > 200 or "\x00" in worker_id:
            raise ValueError("worker_id must be non-empty bounded text.")
        worker = worker_id.strip()
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("expected_version must be a non-negative integer.")
        if not isinstance(message, str) or not message.strip() or len(message) > 1000 or "\x00" in message:
            raise ValueError("message must be non-empty bounded text.")
        if percent is not None and (type(percent) is not int or not 0 <= percent <= 100):
            raise ValueError("percent must be an integer from 0 to 100.")
        run = self.store.get(task_id)
        if run is None:
            raise KeyError("task not found.")
        if run.state != "running":
            raise WorkflowTransitionError("progress can only be recorded for a running task.")
        if run.lease_owner != worker:
            raise WorkflowTransitionError("only the current worker may record task progress.")
        if not run.lease_expires_at:
            raise WorkflowConflict("task has no active worker lease.")
        try:
            lease_expiry = datetime.fromisoformat(run.lease_expires_at)
        except (TypeError, ValueError) as exc:
            raise WorkflowConflict("task lease expiry is invalid.") from exc
        if lease_expiry.tzinfo is None or lease_expiry <= datetime.now(timezone.utc):
            raise WorkflowConflict("expired or invalid lease cannot record task progress.")
        lease_generation = self._verify_worker_credential(run, worker, workload_credential, "task:progress")
        self.store.append_task_event(task_id, kind="task.progress", actor_id=actor,
                                     payload={"worker_id": worker, "message": message.strip(), "percent": percent},
                                     expected_version=expected_version, expected_lease_owner=worker,
                                     expected_lease_generation=lease_generation)
        return self._record(self.store.get(task_id))

    def renew_lease(
        self, *, actor_id: str, task_id: str, worker_id: str,
        expected_version: int, lease_seconds: int = 60,
        workload_credential: str | None = None,
    ) -> TaskRecord:
        """Renew the current worker lease with authorization and compare-and-swap."""
        actor = self._actor(actor_id)
        if not self._allowed(actor, "task:lease:renew", task_id):
            raise PermissionError("task lease renewal is not authorized.")
        if not isinstance(worker_id, str) or not worker_id.strip() or len(worker_id) > 200 or "\x00" in worker_id:
            raise ValueError("worker_id must be non-empty bounded text.")
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("expected_version must be a non-negative integer.")
        current = self.store.get(task_id)
        if current is None:
            raise KeyError("task not found.")
        worker = self._require_active_lease(current, worker_id, "renew")
        self._verify_worker_credential(current, worker, workload_credential, "task:lease:renew")
        renewed = self.store.renew_lease(
            task_id, worker_id=worker,
            expected_version=expected_version, lease_seconds=lease_seconds,
        )
        return self._record(renewed)

    def record_artifact(self, *, actor_id: str, task_id: str, worker_id: str,
                        expected_version: int, artifact_type: str, reference: str,
                        metadata: dict[str, Any] | None = None,
                        workload_credential: str | None = None) -> TaskRecord:
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
        worker = self._require_active_lease(run, worker_id, "attach artifacts to")
        lease_generation = self._verify_worker_credential(run, worker, workload_credential, "task:artifact")
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("expected_version must be a non-negative integer.")
        self.store.append_task_event(task_id, kind="task.artifact", actor_id=actor,
                                     payload={"worker_id": worker, "artifact_type": artifact_type,
                                              "reference": reference.strip(), "metadata": metadata},
                                     expected_version=expected_version, expected_lease_owner=worker,
                                     expected_lease_generation=lease_generation)
        return self._record(self.store.get(task_id))

    def complete(self, *, actor_id: str, task_id: str, expected_version: int,
                 worker_id: str, result: dict[str, Any] | list[Any],
                 workload_credential: str | None = None) -> TaskRecord:
        actor = self._actor(actor_id)
        if not self._allowed(actor, "task:complete", task_id):
            raise PermissionError("task completion is not authorized.")
        run = self.store.get(task_id)
        if run is None:
            raise KeyError("task not found.")
        worker = self._require_active_lease(run, worker_id, "complete")
        lease_generation = self._verify_worker_credential(run, worker, workload_credential, "task:complete")
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("expected_version must be a non-negative integer.")
        completed = self.store.transition_with_event(
            task_id, expected_version=expected_version, to_state="succeeded",
            result=result, event_kind="task.completed", actor_id=actor,
            event_payload={"state_version": expected_version + 1, "worker_id": worker},
            expected_lease_owner=worker, expected_lease_generation=lease_generation,
        )
        return self._record(completed)

    def fail(self, *, actor_id: str, task_id: str, expected_version: int,
             worker_id: str, error_code: str,
             workload_credential: str | None = None) -> TaskRecord:
        actor = self._actor(actor_id)
        if not self._allowed(actor, "task:fail", task_id):
            raise PermissionError("task failure update is not authorized.")
        run = self.store.get(task_id)
        if run is None:
            raise KeyError("task not found.")
        worker = self._require_active_lease(run, worker_id, "fail")
        lease_generation = self._verify_worker_credential(run, worker, workload_credential, "task:fail")
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("expected_version must be a non-negative integer.")
        if not isinstance(error_code, str) or not _CODE.fullmatch(error_code):
            raise ValueError("error_code must be a bounded identifier.")
        failed = self.store.transition_with_event(
            task_id, expected_version=expected_version, to_state="failed",
            error_code=error_code, event_kind="task.failed", actor_id=actor,
            event_payload={"error_code": error_code, "state_version": expected_version + 1,
                           "worker_id": worker},
            expected_lease_owner=worker, expected_lease_generation=lease_generation,
        )
        return self._record(failed)



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
        interrupted = self.store.recover_expired_with_events(actor_id=actor, limit=limit)
        return [self._record(run) for run in interrupted]

    def cancel(self, *, actor_id: str, task_id: str, expected_version: int) -> TaskRecord:
        actor = self._actor(actor_id)
        if not self._allowed(actor, "task:cancel", task_id):
            raise PermissionError("task cancellation is not authorized.")
        run = self.store.get(task_id)
        if run is None:
            raise KeyError("task not found.")
        cancelled = self.store.transition_with_event(
            task_id, expected_version=expected_version, to_state="cancelled",
            error_code="cancelled", event_kind="task.cancelled", actor_id=actor,
            event_payload={"state_version": expected_version + 1},
        )
        return self._record(cancelled)
