"""Guarded bridge between Yuki's persistent queue and registered task handlers.

The orchestrator does not infer handler names from payloads, execute arbitrary code,
or grant permissions. It dispatches only an explicitly configured handler and checks
authorization both at submission and immediately before execution.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from core.job_queue import Job, SQLiteJobQueue

Authorizer = Callable[[str, dict[str, Any]], bool]
Handler = Callable[[dict[str, Any]], dict[str, Any]]

class QueuedTaskOrchestrator:
    """Single-step queue dispatcher; worker-pool sizing is owned by the host runtime."""

    def __init__(
        self,
        queue: SQLiteJobQueue,
        handlers: Mapping[str, Handler],
        authorizer: Authorizer,
        *,
        worker_id: str,
        lease_seconds: int = 120,
    ):
        if not isinstance(worker_id, str) or not worker_id.strip() or len(worker_id) > 120:
            raise ValueError("worker_id must be non-empty and at most 120 characters.")
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or not 1 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be between 1 and 3600.")
        if not callable(authorizer):
            raise TypeError("authorizer must be callable.")
        safe_handlers = {}
        for name, handler in handlers.items():
            if not isinstance(name, str) or not name.strip() or not callable(handler):
                raise ValueError("Handlers must map non-empty task names to callables.")
            safe_handlers[name.strip()] = handler
        self.queue = queue
        self.handlers = safe_handlers
        self.authorizer = authorizer
        self.worker_id = worker_id.strip()
        self.lease_seconds = lease_seconds

    def submit(self, task_type: str, payload: dict[str, Any], *, max_attempts: int = 3) -> Job:
        if task_type not in self.handlers:
            raise ValueError("Task type is not registered.")
        if self.authorizer(task_type, payload) is not True:
            raise PermissionError("Task submission was not authorized.")
        return self.queue.enqueue(task_type, payload, max_attempts=max_attempts)

    def run_one(self) -> Job | None:
        """Claim and dispatch one job. Returns None when no job is available."""
        job = self.queue.claim_next(self.worker_id, lease_seconds=self.lease_seconds)
        if job is None:
            return None
        handler = self.handlers.get(job.task_type)
        if handler is None:
            return self.queue.fail(job.id, self.worker_id, "No handler registered for task type.", retry=False)
        try:
            # Re-check immediately before handler execution; never trust enqueue-time approval alone.
            if self.authorizer(job.task_type, job.payload) is not True:
                return self.queue.fail(job.id, self.worker_id, "Execution authorization denied.", retry=False)
            result = handler(dict(job.payload))
            if not isinstance(result, dict):
                raise TypeError("Task handler must return a JSON-object result.")
            return self.queue.complete(job.id, self.worker_id, result)
        except Exception as exc:
            # Avoid storing tracebacks or arbitrary exception data that may contain secrets.
            message = f"{type(exc).__name__}: task handler failed"
            return self.queue.fail(job.id, self.worker_id, message, retry=True)
