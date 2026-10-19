"""Bounded worker-pool lifecycle over Yuki's persistent guarded task queue.

Handlers must be trusted callables with their own I/O deadlines. This service
never dynamically imports handler names or bypasses the orchestrator authorizer.
"""
from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable, Mapping
from typing import Any

from core.job_queue import Job, SQLiteJobQueue
from core.queued_orchestrator import QueuedTaskOrchestrator


class RuntimeService:
    def __init__(
        self,
        queue: SQLiteJobQueue,
        handlers: Mapping[str, Callable[[dict[str, Any]], dict[str, Any]]],
        authorizer: Callable[[str, dict[str, Any]], bool],
        *,
        max_workers: int = 2,
        lease_seconds: int = 120,
        idle_poll_seconds: float = 0.2,
        shutdown_timeout_seconds: float = 10.0,
    ):
        if type(max_workers) is not int or not 1 <= max_workers <= 32:
            raise ValueError("max_workers must be from 1 to 32")
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be from 1 to 3600")
        if isinstance(idle_poll_seconds, bool) or not isinstance(idle_poll_seconds, (int, float)) or not 0.01 <= idle_poll_seconds <= 5:
            raise ValueError("idle_poll_seconds must be from 0.01 to 5")
        if isinstance(shutdown_timeout_seconds, bool) or not isinstance(shutdown_timeout_seconds, (int, float)) or not 0.1 <= shutdown_timeout_seconds <= 120:
            raise ValueError("shutdown_timeout_seconds must be from 0.1 to 120")
        if not callable(authorizer):
            raise TypeError("authorizer must be callable")
        if not handlers or any(not isinstance(k, str) or not k.strip() or not callable(v) for k, v in handlers.items()):
            raise ValueError("handlers must map registered task names to callables")
        self.queue = queue
        self.handlers = dict(handlers)
        self.authorizer = authorizer
        self.max_workers = max_workers
        self.lease_seconds = lease_seconds
        self.idle_poll_seconds = float(idle_poll_seconds)
        self.shutdown_timeout_seconds = float(shutdown_timeout_seconds)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._started = False
        self._closed = False
        self._last_jobs: dict[str, Job] = {}

    def _orchestrator(self, worker_id: str) -> QueuedTaskOrchestrator:
        return QueuedTaskOrchestrator(
            self.queue, self.handlers, self.authorizer,
            worker_id=worker_id, lease_seconds=self.lease_seconds,
        )

    def submit(self, task_type: str, payload: dict[str, Any], *, max_attempts: int = 3) -> Job:
        with self._lock:
            if self._closed:
                raise RuntimeError("runtime service is closed")
        return self._orchestrator("submitter").submit(task_type, payload, max_attempts=max_attempts)

    def run_one(self, *, worker_id: str | None = None) -> Job | None:
        with self._lock:
            if self._closed:
                raise RuntimeError("runtime service is closed")
        identity = worker_id or f"worker-{uuid.uuid4().hex}"
        return self._orchestrator(identity).run_one()

    def start(self) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("runtime service is closed")
            if self._started:
                return
            self._started = True
            self._stop.clear()
            for index in range(self.max_workers):
                worker_id = f"yuki-worker-{index + 1}-{uuid.uuid4().hex[:8]}"
                thread = threading.Thread(
                    target=self._worker_loop, args=(worker_id,),
                    name=worker_id, daemon=False,
                )
                self._threads.append(thread)
                thread.start()

    def _worker_loop(self, worker_id: str) -> None:
        orchestrator = self._orchestrator(worker_id)
        while not self._stop.is_set():
            try:
                job = orchestrator.run_one()
                if job is not None:
                    with self._lock:
                        self._last_jobs[worker_id] = job
                else:
                    self._stop.wait(self.idle_poll_seconds)
            except Exception:
                # Keep the worker alive for transient queue errors, but back off.
                self._stop.wait(min(1.0, self.idle_poll_seconds * 2))

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "started": self._started,
                "closed": self._closed,
                "worker_count": sum(thread.is_alive() for thread in self._threads),
                "max_workers": self.max_workers,
                "last_jobs": {
                    worker_id: {"job_id": job.id, "status": job.status, "task_type": job.task_type}
                    for worker_id, job in self._last_jobs.items()
                },
            }

    def close(self, *, timeout_seconds: float | None = None) -> bool:
        timeout = self.shutdown_timeout_seconds if timeout_seconds is None else timeout_seconds
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0.1 <= timeout <= 120:
            raise ValueError("timeout_seconds must be from 0.1 to 120")
        with self._lock:
            if self._closed:
                return not any(thread.is_alive() for thread in self._threads)
            self._closed = True
            self._stop.set()
            threads = list(self._threads)
        deadline = time.monotonic() + float(timeout)
        for thread in threads:
            thread.join(max(0.0, deadline - time.monotonic()))
        return not any(thread.is_alive() for thread in threads)

    def __enter__(self) -> "RuntimeService":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
