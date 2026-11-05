"""Bounded worker interface for executing explicitly registered Yuki task handlers.

Handlers are application code, not dynamically imported from task payloads. This
module provides cooperative cancellation/deadline checks; handlers must still
sandbox subprocesses and enforce provider/tool permissions and token budgets.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from automation.agent_execution_observability import AgentExecutionTraceStore
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
    """Executes allowlisted handlers and optionally emits privacy-safe traces.

    Trace persistence is best-effort: telemetry outages never change task
    execution semantics. The trace store accepts identifiers and counters only,
    never task payloads, handler results, or exception messages.
    """

    def __init__(
        self,
        store: TaskStore,
        *,
        worker_id: str,
        handlers: dict[str, Handler],
        lease_seconds: int = 60,
        trace_store: AgentExecutionTraceStore | None = None,
    ):
        if not isinstance(worker_id, str) or not worker_id.strip() or len(worker_id) > 200:
            raise ValueError("worker_id must be non-empty text of at most 200 characters")
        if not isinstance(handlers, dict) or not handlers:
            raise ValueError("handlers must be a non-empty task-type mapping")
        for task_type, handler in handlers.items():
            if not isinstance(task_type, str) or not task_type.strip() or len(task_type) > 120:
                raise ValueError("handler task types must be non-empty text of at most 120 characters")
            if not callable(handler):
                raise TypeError("every registered task handler must be callable")
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or not 1 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be an integer between 1 and 3600")
        if trace_store is not None and not isinstance(trace_store, AgentExecutionTraceStore):
            raise TypeError("trace_store must be an AgentExecutionTraceStore")
        self.store = store
        self.worker_id = worker_id.strip()
        self.handlers = dict(handlers)
        self.lease_seconds = lease_seconds
        self.trace_store = trace_store
        self.trace_errors = 0

    def _trace_start(self, task: dict[str, Any]) -> str | None:
        if self.trace_store is None:
            return None
        try:
            trace = self.trace_store.get_execution_by_task(
                task["id"], agent_id=task["task_type"],
            )
            if trace is None:
                trace = self.trace_store.start_execution(
                    agent_id=task["task_type"], task_id=task["id"],
                )
            self.trace_store.record_event(
                execution_id=trace["execution_id"], event_type="execution.started",
                actor_id=self.worker_id, status="running",
            )
            return trace["execution_id"]
        except Exception:
            self.trace_errors += 1
            return None

    def _trace_event(self, execution_id: str | None, event_type: str, *, status: str | None = None) -> None:
        if self.trace_store is None or execution_id is None:
            return
        try:
            self.trace_store.record_event(
                execution_id=execution_id, event_type=event_type,
                actor_id=self.worker_id, status=status,
            )
        except Exception:
            self.trace_errors += 1

    def run_once(self) -> WorkerOutcome:
        task = self.store.claim_next(self.worker_id, lease_seconds=self.lease_seconds)
        if task is None:
            return WorkerOutcome(task_id=None, status="idle")
        trace_id = self._trace_start(task)
        handler = self.handlers.get(task["task_type"])
        if handler is None:
            finished = self.store.fail(
                task["id"], self.worker_id, "No allowlisted handler for task type",
                retryable=False,
            )
            self._trace_event(trace_id, "execution.failed", status="failed")
            return WorkerOutcome(task_id=task["id"], status=finished["status"], error_type="HandlerNotFound")

        started = time.monotonic()
        context = TaskContext(self.store, task, monotonic_start=started)
        try:
            context.check_cancelled()
            result = handler(task["payload"], context)
            context.check_cancelled()
            finished = self.store.complete(task["id"], self.worker_id, result)
            self._trace_event(
                trace_id, "execution.completed",
                status="succeeded" if finished["status"] == "succeeded" else None,
            )
            return WorkerOutcome(task_id=task["id"], status=finished["status"])
        except TaskCancelled:
            current = self.store.get(task["id"])
            self._trace_event(trace_id, "execution.cancelled", status="cancelled")
            return WorkerOutcome(task_id=task["id"], status=current["status"] if current else "cancelled",
                                 error_type="TaskCancelled")
        except Exception as exc:
            # Do not persist exception messages, which may contain prompts,
            # credentials, paths or provider responses. Keep only the type.
            current = self.store.get(task["id"])
            if current is None or current["status"] != "running" or current.get("worker_id") != self.worker_id:
                self._trace_event(trace_id, "execution.failed")
                return WorkerOutcome(task_id=task["id"], status=current["status"] if current else "unknown",
                                     error_type=type(exc).__name__)
            retryable = not isinstance(exc, (TaskDeadlineExceeded, ValueError, TypeError))
            try:
                finished = self.store.fail(
                    task["id"], self.worker_id, f"Handler failed: {type(exc).__name__}",
                    retryable=retryable,
                )
                self._trace_event(trace_id, "execution.failed",
                                  status="failed" if finished["status"] == "failed" else None)
                return WorkerOutcome(task_id=task["id"], status=finished["status"],
                                     error_type=type(exc).__name__)
            except (KeyError, ValueError):
                current = self.store.get(task["id"])
                self._trace_event(trace_id, "execution.failed")
                return WorkerOutcome(task_id=task["id"], status=current["status"] if current else "unknown",
                                     error_type=type(exc).__name__)
