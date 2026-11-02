"""Durable adapter from agent lifecycle callbacks to the SQLite task journal.

Only bounded lifecycle metadata is persisted. Tool arguments, model prompts, and
tool outputs are deliberately excluded from checkpoint event payloads.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from automation.task_runtime import TaskRuntime
from automation.workflow_store import WorkflowConflict, WorkflowTransitionError


_EVENT_MESSAGES = {
    "agent.started": ("Agent execution started", 0),
    "agent.tools_dispatched": ("Authorized tool batch completed", None),
    "agent.model_failed": ("Model request failed", None),
    "agent.tool_dispatch_failed": ("Guarded tool dispatch failed", None),
    "agent.completed": ("Agent execution completed", 100),
    "agent.stopped": ("Agent execution stopped at a configured boundary", None),
}


class AgentTaskJournal:
    """Persist safe agent lifecycle checkpoints against an active worker lease.

    Instantiate after TaskRuntime has claimed a task. The adapter refreshes the
    task version on construction, then uses compare-and-swap progress events.
    A stale version, expired lease, lost worker ownership, or authorization
    failure stops execution by raising to the agent loop's event callback.
    """

    def __init__(
        self,
        task_runtime: TaskRuntime,
        *,
        actor_id: str,
        task_id: str,
        worker_id: str,
    ) -> None:
        if not isinstance(task_runtime, TaskRuntime):
            raise TypeError("task_runtime must be a TaskRuntime.")
        for value, label in ((actor_id, "actor_id"), (task_id, "task_id"),
                             (worker_id, "worker_id")):
            if not isinstance(value, str) or not value.strip() or len(value) > 200:
                raise ValueError(f"{label} must be non-empty bounded text.")
        self.task_runtime = task_runtime
        self.actor_id = actor_id.strip()
        self.task_id = task_id.strip()
        self.worker_id = worker_id.strip()
        current = self.task_runtime.get(actor_id=self.actor_id, task_id=self.task_id)
        self._assert_active_lease(current)
        self._version = current.version

    def _assert_active_lease(self, task: Any) -> None:
        if task.state != "running" or task.lease_owner != self.worker_id:
            raise WorkflowTransitionError("agent journal requires the current task worker lease.")
        if not task.lease_expires_at:
            raise WorkflowConflict("task worker lease is missing.")
        expiry = datetime.fromisoformat(task.lease_expires_at)
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if expiry <= datetime.now(timezone.utc):
            raise WorkflowConflict("task worker lease has expired.")

    def __call__(self, event: str, details: dict[str, Any]) -> None:
        if event not in _EVENT_MESSAGES:
            raise ValueError("unsupported agent journal event.")
        if not isinstance(details, dict):
            raise ValueError("event details must be an object.")
        current = self.task_runtime.get(actor_id=self.actor_id, task_id=self.task_id)
        self._assert_active_lease(current)
        if current.version != self._version:
            raise WorkflowConflict("task changed outside this agent journal; reload before continuing.")
        message, percent = _EVENT_MESSAGES[event]
        safe_details = {}
        for key in ("model_turns", "tool_calls", "count", "total", "reason", "limit", "error_type"):
            value = details.get(key)
            if isinstance(value, (str, int, float, bool)) and len(str(value)) <= 100:
                safe_details[key] = value
        if safe_details:
            message += ": " + ", ".join(f"{key}={value}" for key, value in sorted(safe_details.items()))
        updated = self.task_runtime.record_progress(
            actor_id=self.actor_id,
            task_id=self.task_id,
            expected_version=self._version,
            message=message[:1000],
            percent=percent,
        )
        self._version = updated.version

    def renew_lease(self, *, lease_seconds: int = 60) -> int:
        """Renew this journal's worker lease and advance its CAS version.

        The task runtime authorizer must grant task:lease:renew. The underlying
        store refuses renewal by stale workers or after lease expiry.
        """
        current = self.task_runtime.renew_lease(
            actor_id=self.actor_id,
            task_id=self.task_id,
            worker_id=self.worker_id,
            expected_version=self._version,
            lease_seconds=lease_seconds,
        )
        self._version = current.version
        return self._version

    @property
    def version(self) -> int:
        """Latest task version observed after a durable checkpoint."""
        return self._version
