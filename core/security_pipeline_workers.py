"""Supervised, bounded background workers for the security operations pipeline.

The host supplies source adapters, provider senders, and process lifecycle hooks.
This module owns scheduling, liveness heartbeats, retry pacing, and shutdown only.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

from core.security_operations_health import SecurityWorkerHeartbeat


class SecurityPipelineWorker:
    """Runs one bounded pipeline step repeatedly with bounded retry backoff."""

    _SUCCESS_STATES = frozenset({"ok", "empty", "busy"})

    def __init__(
        self,
        role: str,
        worker_id: str,
        operation: Callable[[], object],
        heartbeats: SecurityWorkerHeartbeat,
        *,
        interval_seconds: float = 5.0,
        base_retry_seconds: float = 1.0,
        max_retry_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        if role not in SecurityWorkerHeartbeat.ROLES:
            raise ValueError("security_worker_role_invalid")
        if not isinstance(worker_id, str) or not worker_id or len(worker_id) > 96:
            raise ValueError("security_worker_id_invalid")
        if not callable(operation) or not callable(clock) or not callable(getattr(heartbeats, "beat", None)):
            raise TypeError("security_worker_dependencies_required")
        for name, value in (("interval_seconds", interval_seconds),
                            ("base_retry_seconds", base_retry_seconds),
                            ("max_retry_seconds", max_retry_seconds)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                raise ValueError("security_worker_" + name + "_invalid")
        if max_retry_seconds < base_retry_seconds or max_retry_seconds > 3600 or interval_seconds > 3600:
            raise ValueError("security_worker_timing_bounds_invalid")
        self.role = role
        self.worker_id = worker_id
        self.operation = operation
        self.heartbeats = heartbeats
        self.interval_seconds = float(interval_seconds)
        self.base_retry_seconds = float(base_retry_seconds)
        self.max_retry_seconds = float(max_retry_seconds)
        self.clock = clock
        self.stop_event = threading.Event()
        self._failures = 0
        self._last_success_at = None

    @property
    def last_success_at(self):
        return self._last_success_at

    @property
    def consecutive_failures(self):
        return self._failures

    def run_once(self) -> dict:
        """Run one operation; never persist or return exception text."""
        try:
            result = self.operation()
            status = result.get("status", "ok") if isinstance(result, dict) else "ok"
            if status not in self._SUCCESS_STATES:
                raise ValueError("security_worker_operation_status_invalid")
            self.heartbeats.beat(self.role, self.worker_id)
            self._failures = 0
            self._last_success_at = self.clock()
            return {"status": status, "role": self.role}
        except Exception:
            self._failures = min(self._failures + 1, 32)
            return {"status": "failed", "role": self.role, "retry_in_seconds": self.retry_delay()}

    def retry_delay(self) -> float:
        exponent = min(max(self._failures - 1, 0), 20)
        return min(self.max_retry_seconds, self.base_retry_seconds * (2 ** exponent))

    def run_forever(self) -> None:
        """Run until stop is requested; wait is interruptible for prompt shutdown."""
        while not self.stop_event.is_set():
            result = self.run_once()
            delay = self.interval_seconds if result["status"] != "failed" else self.retry_delay()
            if self.stop_event.wait(delay):
                break

    def request_stop(self) -> None:
        self.stop_event.set()


class SecurityPipelineSupervisor:
    """Owns worker threads and provides bounded, graceful process shutdown."""

    def __init__(self, workers):
        if not isinstance(workers, (tuple, list)) or not workers:
            raise ValueError("security_supervisor_workers_required")
        if any(not isinstance(worker, SecurityPipelineWorker) for worker in workers):
            raise TypeError("security_supervisor_worker_invalid")
        roles = [worker.role for worker in workers]
        if len(set(roles)) != len(roles):
            raise ValueError("security_supervisor_roles_must_be_unique")
        self.workers = tuple(workers)
        self._threads = {}
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self._threads:
                raise RuntimeError("security_supervisor_already_started")
            for worker in self.workers:
                thread = threading.Thread(
                    target=worker.run_forever,
                    name="yuki-security-" + worker.role,
                    daemon=False,
                )
                self._threads[worker.role] = thread
                thread.start()

    def stop(self, *, timeout_seconds: float = 10.0) -> dict:
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not 0 < timeout_seconds <= 300:
            raise ValueError("security_supervisor_shutdown_timeout_invalid")
        for worker in self.workers:
            worker.request_stop()
        deadline = time.monotonic() + float(timeout_seconds)
        for thread in tuple(self._threads.values()):
            remaining = max(0.0, deadline - time.monotonic())
            thread.join(remaining)
        return {
            "stopped": all(not thread.is_alive() for thread in self._threads.values()),
            "workers": {role: not thread.is_alive() for role, thread in self._threads.items()},
        }

    def snapshot(self) -> dict:
        return {
            "started": bool(self._threads),
            "workers": {
                worker.role: {
                    "thread_alive": self._threads.get(worker.role).is_alive()
                    if worker.role in self._threads else False,
                    "consecutive_failures": worker.consecutive_failures,
                }
                for worker in self.workers
            },
        }
