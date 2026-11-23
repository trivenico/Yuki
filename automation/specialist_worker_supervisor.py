"""Explicit lifecycle supervisor for registered specialist workers.

Importing this module starts no threads. The host service must invoke
run_until_shutdown() from its chosen process/thread lifecycle.
"""
from __future__ import annotations

from threading import Event, Lock
from time import monotonic
from typing import Callable, Iterable

from automation.registered_specialist_worker import RegisteredSpecialistWorker


class SpecialistWorkerSupervisor:
    """Own a bounded set of registered workers and coordinate graceful shutdown."""

    def __init__(
        self,
        workers: Iterable[RegisteredSpecialistWorker],
        *,
        idle_sleep_seconds: float = 0.25,
        recovery_callback: Callable[[], object] | None = None,
        recovery_interval_seconds: float = 15.0,
    ):
        items = tuple(workers)
        if not 1 <= len(items) <= 64:
            raise ValueError("supervisor requires between 1 and 64 workers.")
        if any(not isinstance(item, RegisteredSpecialistWorker) for item in items):
            raise TypeError("all supervised workers must be RegisteredSpecialistWorker instances.")
        ids = [item.worker.worker_id for item in items]
        if len(set(ids)) != len(ids):
            raise ValueError("supervised worker IDs must be unique.")
        if (
            isinstance(idle_sleep_seconds, bool)
            or not isinstance(idle_sleep_seconds, (int, float))
            or not 0.01 <= idle_sleep_seconds <= 30
        ):
            raise ValueError("idle_sleep_seconds must be between 0.01 and 30 seconds.")
        if recovery_callback is not None and not callable(recovery_callback):
            raise TypeError("recovery_callback must be callable.")
        if (
            isinstance(recovery_interval_seconds, bool)
            or not isinstance(recovery_interval_seconds, (int, float))
            or not 0.1 <= recovery_interval_seconds <= 3600
        ):
            raise ValueError("recovery_interval_seconds must be between 0.1 and 3600 seconds.")
        self.workers = items
        self.idle_sleep_seconds = float(idle_sleep_seconds)
        self.recovery_callback = recovery_callback
        self.recovery_interval_seconds = float(recovery_interval_seconds)
        self._last_recovery_at: float | None = None
        self._last_recovery_count = 0
        self._shutdown = Event()
        self._lock = Lock()
        self._running = False
        self._closed = False
        self._shutdown_sessions_fenced = False

    @property
    def last_recovery_count(self) -> int:
        return self._last_recovery_count

    def _run_recovery_if_due(self) -> None:
        if self.recovery_callback is None:
            return
        now = monotonic()
        if (
            self._last_recovery_at is not None
            and now - self._last_recovery_at < self.recovery_interval_seconds
        ):
            return
        # The callback must be a pre-authorized TaskRuntime.recover_expired
        # invocation. It interrupts only expired leases; it never requeues work.
        recovered = self.recovery_callback()
        if not isinstance(recovered, (list, tuple)):
            raise TypeError("recovery_callback must return a list or tuple of recovered tasks.")
        self._last_recovery_count = len(recovered)
        self._last_recovery_at = monotonic()

    def request_shutdown(self) -> None:
        """Fence sessions immediately so active sandboxes observe cancellation.

        Python handlers cannot be forcibly stopped safely. Their late results are
        rejected by durable task-lease/session checks after this fence.
        """
        self._shutdown.set()
        with self._lock:
            if self._shutdown_sessions_fenced:
                return
            self._shutdown_sessions_fenced = True
        errors = []
        for worker in reversed(self.workers):
            try:
                worker.close()
            except Exception:
                errors.append("worker_shutdown_failed")
        if errors:
            raise RuntimeError("one or more worker sessions could not be fenced for shutdown.")

    def run_until_shutdown(self) -> None:
        """Register workers, drain approved work, and close sessions on exit."""
        with self._lock:
            if self._closed:
                raise RuntimeError("worker supervisor is closed.")
            if self._running:
                raise RuntimeError("worker supervisor is already running.")
            self._running = True
        started = []
        try:
            for worker in self.workers:
                worker.start()
                started.append(worker)
            while not self._shutdown.is_set():
                self._run_recovery_if_due()
                if self._shutdown.is_set():
                    break
                did_work = False
                for worker in self.workers:
                    if self._shutdown.is_set():
                        break
                    if worker.run_once() is not None:
                        did_work = True
                if not did_work and not self._shutdown.wait(self.idle_sleep_seconds):
                    continue
        finally:
            errors = []
            for worker in reversed(started):
                try:
                    worker.close()
                except Exception:
                    errors.append("worker_shutdown_failed")
            with self._lock:
                self._running = False
                self._closed = True
            if errors:
                raise RuntimeError("one or more supervised workers failed to shut down.")

    def close(self) -> None:
        """Request shutdown; close immediately only when the loop is not running."""
        self.request_shutdown()
        with self._lock:
            if self._running:
                return
            # Repeated close() calls retry workers whose earlier heartbeat join
            # timed out; worker adapters remain fenced while cleanup is pending.
            self._closed = True
        errors = []
        for worker in reversed(self.workers):
            try:
                worker.close()
            except Exception:
                errors.append("worker_shutdown_failed")
        if errors:
            raise RuntimeError("one or more supervised workers failed to shut down.")
