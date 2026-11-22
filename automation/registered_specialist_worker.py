"""Lifecycle adapter that publishes specialist-worker health to WorkerRegistry.

Registry failures affect health reporting only; task authorization and leases remain
owned by TaskRuntime. This adapter never changes task permissions or lease state.
"""
from __future__ import annotations

from threading import Event, Lock, Thread, current_thread
from typing import Any

from automation.specialist_worker import SpecialistTaskWorker
from automation.worker_registry import WorkerRegistry, WorkerSession, WorkerSessionError


class RegisteredSpecialistWorker:
    """Own a specialist worker and its durable operational registration."""

    def __init__(
        self,
        worker: SpecialistTaskWorker,
        registry: WorkerRegistry,
        *,
        heartbeat_interval: float = 10.0,
        metadata: dict[str, Any] | None = None,
    ):
        if not isinstance(worker, SpecialistTaskWorker):
            raise TypeError("worker must be a SpecialistTaskWorker.")
        if not isinstance(registry, WorkerRegistry):
            raise TypeError("registry must be a WorkerRegistry.")
        if (
            isinstance(heartbeat_interval, bool)
            or not isinstance(heartbeat_interval, (int, float))
            or not 0.1 <= heartbeat_interval <= 300
        ):
            raise ValueError("heartbeat_interval must be between 0.1 and 300 seconds.")
        self.worker = worker
        self.registry = registry
        self.heartbeat_interval = float(heartbeat_interval)
        self.metadata = metadata
        self._lock = Lock()
        self._stop = Event()
        self._thread: Thread | None = None
        self._session: WorkerSession | None = None
        self._started = False
        self._closed = False
        self._last_heartbeat: str | None = None
        self._heartbeat_error: str | None = None

    def start(self) -> None:
        """Register this process session and start its durable heartbeat."""
        with self._lock:
            if self._closed:
                raise RuntimeError("registered worker is closed.")
            if self._started:
                return
            session = self.registry.register(self.worker.worker_id, metadata=self.metadata)
            self._session = session
            self._started = True
            self._stop.clear()
            self._thread = Thread(
                target=self._heartbeat_loop,
                name="yuki-worker-registry-heartbeat",
                daemon=True,
            )
            self._thread.start()

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self.heartbeat_interval):
            session = self._session
            if session is None:
                return
            try:
                timestamp = self.registry.heartbeat(
                    session.worker_id, session_token=session.session_token,
                )
            except WorkerSessionError:
                with self._lock:
                    self._heartbeat_error = "worker_session_lost"
                return
            except Exception:
                # Do not expose database paths or exception details in health data.
                with self._lock:
                    self._heartbeat_error = "worker_registry_unavailable"
                continue
            with self._lock:
                self._last_heartbeat = timestamp
                self._heartbeat_error = None

    def _ensure_current_session(self) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("registered worker is closed.")
            if self._heartbeat_error == "worker_session_lost":
                raise RuntimeError("worker registry session was replaced.")

    def run_once(self):
        self.start()
        self._ensure_current_session()
        return self.worker.run_once()

    def health_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "worker_id": self.worker.worker_id,
                "registered": self._started and not self._closed,
                "last_registry_heartbeat": self._last_heartbeat,
                "registry_error": self._heartbeat_error,
                "closed": self._closed,
            }

    def close(self, *, join_timeout: float = 5.0) -> None:
        """Stop heartbeat and persist shutdown; idempotent and session-fenced."""
        if (
            isinstance(join_timeout, bool)
            or not isinstance(join_timeout, (int, float))
            or not 0 <= join_timeout <= 30
        ):
            raise ValueError("join_timeout must be between 0 and 30 seconds.")
        with self._lock:
            if self._closed:
                return
            session = self._session
            thread = self._thread
            self._closed = True
            self._stop.set()
        if thread is not None and thread is not current_thread():
            thread.join(timeout=join_timeout)
            if thread.is_alive():
                with self._lock:
                    self._closed = False
                raise RuntimeError("worker registry heartbeat did not stop.")
        if session is not None:
            try:
                self.registry.set_status(
                    session.worker_id, session_token=session.session_token, status="stopping",
                )
                self.registry.set_status(
                    session.worker_id, session_token=session.session_token, status="stopped",
                )
            except WorkerSessionError:
                # A replacement process owns the row now; never mutate its state.
                pass
