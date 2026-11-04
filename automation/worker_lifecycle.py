"""Thread-safe, process-local coordination for graceful workflow worker shutdown.
The coordinator gates new claims and tracks work already claimed by workers.
It does not cancel handlers, mutate durable leases, or replay work.
"""
from __future__ import annotations
from threading import Condition
from time import monotonic

class WorkerShutdownCoordinator:
    """Coordinate bounded shutdown without taking ownership from live workers."""
    MAX_GRACE_SECONDS = 300.0

    def __init__(self) -> None:
        self._condition = Condition()
        self._stopping = False
        self._claim_reservations: set[str] = set()
        self._active: dict[str, str] = {}

    @staticmethod
    def _identity(value: str, field: str) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > 200 or "\x00" in value:
            raise ValueError(f"{field} must be non-empty text of at most 200 characters.")
        return value

    @property
    def stopping(self) -> bool:
        with self._condition:
            return self._stopping

    def try_begin_claim(self, worker_id: str) -> bool:
        """Reserve permission to claim before calling the durable store."""
        worker = self._identity(worker_id, "worker_id")
        with self._condition:
            if self._stopping:
                return False
            if worker in self._claim_reservations or worker in self._active:
                raise RuntimeError("worker already has a claim reservation or active task.")
            self._claim_reservations.add(worker)
            return True

    def register_claim(self, worker_id: str, task_id: str) -> None:
        """Register a successfully claimed durable task against its reservation."""
        worker = self._identity(worker_id, "worker_id")
        task = self._identity(task_id, "task_id")
        with self._condition:
            if worker not in self._claim_reservations:
                raise RuntimeError("worker has no claim reservation.")
            if task in self._active.values():
                raise RuntimeError("task is already registered to an active worker.")
            self._claim_reservations.remove(worker)
            self._active[worker] = task
            self._condition.notify_all()

    def release_claim_reservation(self, worker_id: str) -> None:
        worker = self._identity(worker_id, "worker_id")
        with self._condition:
            if worker not in self._claim_reservations:
                raise RuntimeError("worker has no claim reservation.")
            self._claim_reservations.remove(worker)
            self._condition.notify_all()

    def finish_task(self, worker_id: str, task_id: str) -> None:
        worker = self._identity(worker_id, "worker_id")
        task = self._identity(task_id, "task_id")
        with self._condition:
            if self._active.get(worker) != task:
                raise RuntimeError("worker does not own this active task.")
            del self._active[worker]
            self._condition.notify_all()

    def begin_shutdown(self) -> None:
        """Atomically prevent new claim reservations."""
        with self._condition:
            self._stopping = True
            self._condition.notify_all()

    def wait_for_idle(self, grace_seconds: float) -> bool:
        """Stop new claims and wait at most grace_seconds for reservations/tasks."""
        if isinstance(grace_seconds, bool) or not isinstance(grace_seconds, (int, float)):
            raise ValueError("grace_seconds must be a number.")
        if not 0 <= grace_seconds <= self.MAX_GRACE_SECONDS:
            raise ValueError("grace_seconds must be between 0 and 300 seconds.")
        deadline = monotonic() + float(grace_seconds)
        self.begin_shutdown()
        with self._condition:
            while self._claim_reservations or self._active:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    def snapshot(self) -> dict[str, object]:
        """Return a stable, payload-free operational snapshot."""
        with self._condition:
            return {
                "stopping": self._stopping,
                "claim_reservations": len(self._claim_reservations),
                "active_workers": dict(self._active),
            }
