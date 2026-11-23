"""Lifecycle adapter that publishes specialist-worker health to WorkerRegistry.

Registry failures affect health reporting only; task authorization and leases remain
owned by TaskRuntime. This adapter never changes task permissions or lease state.
"""
from __future__ import annotations

import hashlib
from threading import Event, Lock, Thread, current_thread
from typing import Any, Callable

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
        learning_observer: Callable[[Any], None] | None = None,
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
        if learning_observer is not None and not callable(learning_observer):
            raise TypeError("learning_observer must be callable.")
        self.learning_observer = learning_observer
        self._learning_error: str | None = None
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
            # Validate shared storage before creating a durable registration.
            # Atomic task-claim fencing requires both tables in the same SQLite file.
            runtime_store = getattr(self.worker, "runtime", None)
            workflow_store = getattr(runtime_store, "store", None)
            if workflow_store is not None:
                from pathlib import Path
                if Path(workflow_store.database_path).resolve() != Path(self.registry.database_path).resolve():
                    raise RuntimeError("worker registry and workflow store must share one SQLite database.")
            session = self.registry.register(self.worker.worker_id, metadata=self.metadata)
            self._session = session
            self.worker._registry_session = (session.worker_id, session.session_token)
            self.worker._registry_session_guard = self._ensure_current_session
            self._started = True
            self._stop.clear()
            self._thread = Thread(
                target=self._heartbeat_loop,
                name="yuki-worker-registry-heartbeat",
                daemon=True,
            )
            try:
                self._thread.start()
            except Exception:
                # A failed thread start must not strand an active registry row.
                self.worker._registry_session = None
                self.worker._registry_session_guard = None
                self._session = None
                self._thread = None
                self._started = False
                try:
                    self.registry.set_status(
                        session.worker_id, session_token=session.session_token,
                        status="stopping",
                    )
                    self.registry.set_status(
                        session.worker_id, session_token=session.session_token,
                        status="stopped",
                    )
                except WorkerSessionError:
                    pass
                raise

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
            session = self._session
            if session is None:
                raise RuntimeError("worker registry session is unavailable.")
        try:
            # Heartbeat detection is asynchronous; verify ownership synchronously
            # immediately before every claim to close the replacement race.
            self.registry.assert_current_session(
                session.worker_id, session_token=session.session_token,
            )
        except WorkerSessionError as exc:
            with self._lock:
                self._heartbeat_error = "worker_session_lost"
            raise RuntimeError("worker registry session was replaced.") from exc
        except Exception as exc:
            with self._lock:
                self._heartbeat_error = "worker_registry_unavailable"
            raise RuntimeError("worker registry unavailable; refusing task claim.") from exc
        with self._lock:
            if self._closed:
                raise RuntimeError("registered worker is closed.")

    def _observe_completed_tasks(self, results) -> None:
        if self.learning_observer is None:
            return
        for result in results:
            if result is None or getattr(result, "state", None) != "completed":
                continue
            # The observer must resolve consent and memory by result.owner_id.
            # Learning failures never change an already committed task outcome.
            try:
                self.learning_observer(result)
                self._learning_error = None
            except Exception:
                self._learning_error = "learning_observer_failed"

    def run_once(self):
        self.start()
        self._ensure_current_session()
        result = self.worker.run_once()
        self._observe_completed_tasks((result,))
        return result

    def run_concurrently(self, *, max_workers: int = 4, max_tasks: int = 20):
        """Run a bounded pool with a distinct durable session for every child."""
        self.start()
        self._ensure_current_session()

        def register_child(worker_id: str) -> tuple[str, str]:
            session = self.registry.register(
                worker_id,
                metadata={
                    "role": "specialist_pool_child",
                    "parent_worker_id": self.worker.worker_id,
                    # Store only a digest; the parent's bearer token must never
                    # be copied into child metadata or persisted in SQLite.
                    "parent_session_hash": hashlib.sha256(
                        self._session.session_token.encode("utf-8")
                    ).hexdigest(),
                },
            )
            return session.worker_id, session.session_token

        def assert_child_session(worker_id: str, session: tuple[str, str]) -> None:
            session_worker_id, token = session
            if session_worker_id != worker_id:
                raise RuntimeError("registered child session identity mismatch.")
            self._ensure_current_session()
            self.registry.assert_current_session(worker_id, session_token=token)

        def heartbeat_child(worker_id: str, session: tuple[str, str]) -> None:
            session_worker_id, token = session
            if session_worker_id != worker_id:
                raise RuntimeError("registered child session identity mismatch.")
            parent = self._session
            if parent is None:
                raise WorkerSessionError("parent worker session is unavailable.")
            self.registry.heartbeat_child(
                worker_id, session_token=token,
                parent_worker_id=parent.worker_id,
                parent_session_token=parent.session_token,
            )

        def stop_child(worker_id: str, session: tuple[str, str]) -> None:
            session_worker_id, token = session
            if session_worker_id != worker_id:
                raise RuntimeError("registered child session identity mismatch.")
            try:
                self.registry.set_status(worker_id, session_token=token, status="stopping")
                self.registry.set_status(worker_id, session_token=token, status="stopped")
            except WorkerSessionError:
                # A replacement child session owns the row; do not mutate it.
                return

        return self.worker.run_concurrently(
            max_workers=max_workers,
            max_tasks=max_tasks,
            worker_session_factory=register_child,
            worker_session_cleanup=stop_child,
            worker_session_guard=self._ensure_current_session,
            worker_session_assertion=assert_child_session,
            worker_session_heartbeat=heartbeat_child,
            worker_session_heartbeat_interval=self.heartbeat_interval,
        )
        results = self.worker.run_concurrently(
            max_workers=max_workers,
            max_tasks=max_tasks,
            worker_session_factory=register_child,
            worker_session_cleanup=stop_child,
            worker_session_guard=self._ensure_current_session,
            worker_session_assertion=assert_child_session,
            worker_session_heartbeat=heartbeat_child,
            worker_session_heartbeat_interval=self.heartbeat_interval,
        )
        self._observe_completed_tasks(results)
        return results

    def health_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "worker_id": self.worker.worker_id,
                "registered": self._started and not self._closed,
                "last_registry_heartbeat": self._last_heartbeat,
                "registry_error": self._heartbeat_error,
                "closed": self._closed,
                "learning_observer_error": self._learning_error,
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
            # A previous close may have timed out while the heartbeat was inside
            # SQLite. Keep the worker fenced, but allow a later close() to finish
            # joining and persist the stopped state.
            if self._closed and self._session is None:
                return
            session = self._session
            thread = self._thread
            self._closed = True
            self._stop.set()
        if thread is not None and thread is not current_thread():
            thread.join(timeout=join_timeout)
            if thread.is_alive():
                # Never reopen task claims after shutdown has been requested.
                raise RuntimeError("worker registry heartbeat did not stop.")
        self.worker._registry_session = None
        self.worker._registry_session_guard = None
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
        with self._lock:
            self._session = None
            self._thread = None
            self._started = False
