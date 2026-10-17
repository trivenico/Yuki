"""Single-process durable worker for explicitly trusted, bounded task handlers.

This worker is a local orchestration primitive, not a sandbox. Do not register
third-party/plugin handlers until they run behind an isolated process boundary.
Handlers must poll context.cancelled() during long-running work. Hard termination
of hung handlers is not provided by this in-process worker.
"""
from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Any, Callable

from core.durable_jobs import DurableJob, JobStore


class RetryableTaskError(Exception):
    """A task failure that is safe to retry because the handler guarantees idempotency."""


@dataclass(frozen=True)
class TaskContext:
    job_id: str
    attempt: int
    _store: JobStore

    def cancelled(self) -> bool:
        return self._store.cancellation_requested(self.job_id)


TaskHandler = Callable[[dict[str, Any], TaskContext], Any]


class TaskWorker:
    """One-job-at-a-time worker backed by JobStore."""

    def __init__(self, store: JobStore, *, handlers: dict[str, TaskHandler] | None = None):
        self.store = store
        self._handlers: dict[str, TaskHandler] = {}
        self._lock = threading.Lock()
        for task_type, handler in (handlers or {}).items():
            self.register(task_type, handler)

    def register(self, task_type: str, handler: TaskHandler) -> None:
        if not isinstance(task_type, str) or not task_type.strip() or len(task_type) > 100:
            raise ValueError("task_type must be non-empty bounded text.")
        if not callable(handler):
            raise TypeError("handler must be callable.")
        with self._lock:
            if task_type in self._handlers:
                raise ValueError("Duplicate task handler.")
            self._handlers[task_type] = handler

    def run_once(self) -> DurableJob | None:
        job = self.store.claim_next()
        if job is None:
            return None
        with self._lock:
            handler = self._handlers.get(job.task_type)
        if handler is None:
            return self.store.fail(job.job_id, error_code="handler_not_registered", retryable=False)
        context = TaskContext(job.job_id, job.attempts, self.store)
        try:
            if context.cancelled():
                self.store.request_cancel(job.job_id)
                return self.store.fail(job.job_id, error_code="cancelled_before_start", retryable=False)
            result = handler(job.payload, context)
            if context.cancelled():
                self.store.request_cancel(job.job_id)
                return self.store.fail(job.job_id, error_code="cancelled_during_execution", retryable=False)
            return self.store.finish(job.job_id, result=result)
        except RetryableTaskError:
            return self.store.fail(job.job_id, error_code="retryable_task_error", retryable=True)
        except Exception:
            # Never persist exception messages: they may contain credentials or user data.
            return self.store.fail(job.job_id, error_code="task_handler_failed", retryable=False)
