"""Explicit lifecycle supervisor for registered specialist workers.

Importing this module starts no threads. The host service must invoke
run_until_shutdown() from its chosen process/thread lifecycle.
"""
from __future__ import annotations

from threading import Event, Lock
from typing import Iterable

from automation.registered_specialist_worker import RegisteredSpecialistWorker


class SpecialistWorkerSupervisor:
    """Own a bounded set of registered workers and coordinate graceful shutdown."""

    def __init__(
        self,
        workers: Iterable[RegisteredSpecialistWorker],
        *,
        idle_sleep_seconds: float = 0.25,
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
        self.workers = items
        self.idle_sleep_seconds = float(idle_sleep_seconds)
        self._shutdown = Event()
        self._lock = Lock()
        self._running = False
        self._closed = False

    def request_shutdown(self) -> None:
        """Stop claiming new tasks; the active synchronous handler may finish."""
        self._shutdown.set()

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
            if self._running or self._closed:
                return
            self._closed = True
        errors = []
        for worker in reversed(self.workers):
            try:
                worker.close()
            except Exception:
                errors.append("worker_shutdown_failed")
        if errors:
            raise RuntimeError("one or more supervised workers failed to shut down.")
