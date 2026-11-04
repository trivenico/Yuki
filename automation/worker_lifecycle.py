"""Thread-safe, process-local coordination for graceful workflow worker shutdown.
The coordinator gates new claims and tracks work already claimed by workers.
It does not cancel handlers, mutate durable leases, or replay work.
"""
from __future__ import annotations
from threading import Condition, Event, Thread
from time import monotonic

class WorkerLeaseLost(RuntimeError):
    """Raised when a worker can no longer prove ownership of its durable lease."""


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

    def claim_next(self, store: object, worker_id: str, *, lease_seconds: int = 60):
        """Claim through the durable store while holding the shutdown gate.

        A reservation is acquired before the database claim, so shutdown cannot
        race between the gate check and durable claim. Empty queues and store
        exceptions always release the reservation.
        """
        if not callable(getattr(store, "claim_next", None)):
            raise TypeError("store must provide a callable claim_next method.")
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be an integer from 1 to 3600.")
        if not self.try_begin_claim(worker_id):
            return None
        try:
            claimed = store.claim_next(worker_id=worker_id, lease_seconds=lease_seconds)
            if claimed is None:
                self.release_claim_reservation(worker_id)
                return None
            task_id = getattr(claimed, "run_id", None)
            if not isinstance(task_id, str) or not task_id:
                self.release_claim_reservation(worker_id)
                raise RuntimeError("durable store returned a claim without a valid run_id.")
            self.register_claim(worker_id, task_id)
            return claimed
        except BaseException:
            # If registration already succeeded, the reservation no longer
            # exists; do not mask the original exception.
            with self._condition:
                reserved = worker_id in self._claim_reservations
            if reserved:
                self.release_claim_reservation(worker_id)
            raise

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

    def execute_claimed(self, worker_id: str, claimed: object, handler):
        """Run a registered claim and always release process-local active tracking.

        The handler owns durable completion/failure reporting. If it raises or
        the process exits, this wrapper does not retry or alter the durable
        lease; normal lease expiry and supervisor reconciliation remain in force.
        """
        worker = self._identity(worker_id, "worker_id")
        task_id = self._identity(getattr(claimed, "run_id", None), "task_id")
        if not callable(handler):
            raise TypeError("handler must be callable.")
        with self._condition:
            if self._active.get(worker) != task_id:
                raise RuntimeError("worker does not own this registered claim.")
        try:
            return handler(claimed)
        finally:
            self.finish_task(worker, task_id)

    def execute_claimed_with_heartbeat(
        self, worker_id: str, claimed: object, handler, renew_lease,
        *, heartbeat_interval: float = 10.0,
    ):
        """Execute cooperatively while renewing a durable lease in a helper thread.

        handler(claimed, lease_lost_event) must periodically inspect the event
        and stop initiating side effects when it is set. renew_lease must be
        bounded and return exactly True only after a confirmed renewal.
        A failed/ambiguous renewal signals lease loss; this method never retries
        the handler or reports durable task completion.
        """
        worker = self._identity(worker_id, "worker_id")
        task_id = self._identity(getattr(claimed, "run_id", None), "task_id")
        if not callable(handler) or not callable(renew_lease):
            raise TypeError("handler and renew_lease must be callable.")
        if isinstance(heartbeat_interval, bool) or not isinstance(heartbeat_interval, (int, float)):
            raise ValueError("heartbeat_interval must be a number.")
        if not 0.01 <= heartbeat_interval <= 300:
            raise ValueError("heartbeat_interval must be between 0.01 and 300 seconds.")
        with self._condition:
            if self._active.get(worker) != task_id:
                raise RuntimeError("worker does not own this registered claim.")

        stop = Event()
        lease_lost = Event()
        heartbeat_errors: list[BaseException] = []

        def heartbeat_loop():
            while not stop.wait(float(heartbeat_interval)):
                try:
                    renewed = renew_lease()
                    if renewed is not True:
                        raise WorkerLeaseLost("durable lease renewal was not confirmed.")
                except BaseException as exc:
                    heartbeat_errors.append(exc)
                    lease_lost.set()
                    return

        heartbeat = Thread(
            target=heartbeat_loop, name=f"yuki-lease-heartbeat-{worker}", daemon=True,
        )
        heartbeat.start()
        try:
            result = handler(claimed, lease_lost)
            if lease_lost.is_set():
                cause = heartbeat_errors[0] if heartbeat_errors else None
                raise WorkerLeaseLost(
                    "worker lease was lost; handler result must not be treated as committed."
                ) from cause
            return result
        finally:
            stop.set()
            heartbeat.join()
            self.finish_task(worker, task_id)

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
