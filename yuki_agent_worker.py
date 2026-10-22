"""Bounded worker interface for executing explicitly registered Yuki task handlers.

Handlers are application code, not dynamically imported from task payloads. This
module provides cooperative cancellation/deadline checks; handlers must still
sandbox subprocesses and enforce provider/tool permissions and token budgets.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from yuki_agent_runtime import TaskStore


class TaskCancelled(RuntimeError):
    """Raised by a cooperative handler after a task has been cancelled."""


class TaskDeadlineExceeded(TimeoutError):
    """Raised by a cooperative handler after its task deadline."""


@dataclass(frozen=True)
class WorkerOutcome:
    task_id: str | None
    status: str
    error_type: str | None = None


class TaskContext:
    """Limited task context passed to trusted, explicitly registered handlers."""

    def __init__(self, store: TaskStore, task: dict[str, Any], *, monotonic_start: float):
        self._store = store
        self.task = task
        self._monotonic_start = monotonic_start

    @property
    def budget_seconds(self) -> int | None:
        return self.task.get("budget_seconds")

    @property
    def budget_tokens(self) -> int | None:
        return self.task.get("budget_tokens")

    def check_cancelled(self) -> None:
        current = self._store.get(self.task["id"])
        if current is None or current["status"] == "cancelled":
            raise TaskCancelled("task cancelled")
        if current["status"] != "running":
            raise TaskCancelled("task is no longer running")
        budget = self.budget_seconds
        if budget is not None and time.monotonic() - self._monotonic_start >= budget:
            raise TaskDeadlineExceeded("task execution budget exceeded")
        deadline = current.get("deadline_at")
        if deadline:
            from datetime import datetime, timezone
            if datetime.fromisoformat(deadline) <= datetime.now(timezone.utc):
                raise TaskDeadlineExceeded("task deadline exceeded")


Handler = Callable[[dict[str, Any], TaskContext], Any]


class AgentWorker:
    """Executes only allowlisted handlers and records outcomes in TaskStore."""

    def __init__(
        self,
        store: TaskStore,
        *,
        worker_id: str,
        handlers: dict[str, Handler],
        lease_seconds: int = 60,
    ):
        if not isinstance(worker_id, str) or not worker_id.strip() or len(worker_id) > 200:
            raise ValueError("worker_id must be non-empty text of at most 200 characters")
        if not isinstance(handlers, dict) or not handlers:
            raise ValueError("handlers must be a non-empty task-type mapping")
        for task_type, handler in handlers.items():
            if not isinstance(task_type, str) or not task_type.strip() or len(task_type) > 120:
                raise ValueError("handler task types must be non-empty text of at most 120 characters")
            if not callable(handler):
                raise TypeError("every registered handler must be callable")
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or not 1 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be an integer between 1 and 3600")
        self.store = store
        self.worker_id = worker_id.strip()
        self.handlers = dict(handlers)
        self.lease_seconds = lease_seconds

    def run_once(self) -> WorkerOutcome:
        task = self.store.claim_next(self.worker_id, lease_seconds=self.lease_seconds)
        if task is None:
            return WorkerOutcome(task_id=None, status="idle")
        handler = self.handlers.get(task["task_type"])
        if handler is None:
            # A task with no registered handler is a configuration failure, not
            # an invitation to dynamically import or execute an arbitrary name.
            finished = self.store.fail(
                task["id"], self.worker_id, "No allowlisted handler for task type",
                retryable=False,
            )
            return WorkerOutcome(task_id=task["id"], status=finished["status"], error_type="HandlerNotFound")

        started = time.monotonic()
        context = TaskContext(self.store, task, monotonic_start=started)
        try:
            context.check_cancelled()
            result = handler(task["payload"], context)
            context.check_cancelled()
            finished = self.store.complete(task["id"], self.worker_id, result)
            return WorkerOutcome(task_id=task["id"], status=finished["status"])
        except TaskCancelled:
            current = self.store.get(task["id"])
            return WorkerOutcome(task_id=task["id"], status=current["status"] if current else "cancelled",
                                 error_type="TaskCancelled")
        except Exception as exc:
            # Do not persist exception messages, which may contain prompts,
            # credentials, paths or provider responses. Keep only the type.
            current = self.store.get(task["id"])
            if current is None or current["status"] != "running" or current.get("worker_id") != self.worker_id:
                return WorkerOutcome(task_id=task["id"], status=current["status"] if current else "unknown",
                                     error_type=type(exc).__name__)
            retryable = not isinstance(exc, (TaskDeadlineExceeded, ValueError, TypeError))
            try:
                finished = self.store.fail(
                    task["id"], self.worker_id, f"Handler failed: {type(exc).__name__}",
                    retryable=retryable,
                )
                return WorkerOutcome(task_id=task["id"], status=finished["status"],
                                     error_type=type(exc).__name__)
            except (KeyError, ValueError):
                current = self.store.get(task["id"])
                return WorkerOutcome(task_id=task["id"], status=current["status"] if current else "unknown",
                                     error_type=type(exc).__name__)
