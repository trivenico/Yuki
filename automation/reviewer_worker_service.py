"""Supervise reviewer polling with optional durable cross-process slots.

The executor pool is process-local; when a ReviewerWorkerRegistry is supplied,
each execution must also reserve a shared per-reviewer slot. SQLite outbox
claims remain the duplicate-dispatch guard, while task leases remain the
authority for task-level fencing and recovery.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait, ALL_COMPLETED
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Event, Thread
from types import SimpleNamespace
from typing import Callable

from automation.reviewer_worker_registry import (
    ReviewerWorkerRegistry, ReviewerWorkerRegistryConflict,
)


class ReviewerWorkerServiceError(RuntimeError):
    """The reviewer worker service could not maintain its runtime contract."""


@dataclass(frozen=True)
class ReviewerWorkerHeartbeat:
    worker_id: str
    state: str
    observed_at: str
    active_slots: int
    max_concurrency: int
    completed_dispatches: int
    failed_cycles: int
    last_error_code: str | None


@dataclass(frozen=True)
class ReviewerWorkerServiceReport:
    worker_id: str
    cycles: int
    completed_dispatches: int
    failed_cycles: int
    stopped: bool


class ReviewerWorkerService:
    """Run one reviewer worker with bounded parallel polling and health reports.

    heartbeat_callback must publish to trusted host monitoring. It receives no
    credentials or task payloads. stop() prevents new cycles; in-flight calls
    are allowed to finish before run_forever returns.
    """

    def __init__(
        self,
        worker,
        *,
        heartbeat_callback: Callable[[ReviewerWorkerHeartbeat], None],
        max_concurrency: int = 1,
        poll_interval_seconds: float = 2.0,
        registry: ReviewerWorkerRegistry | None = None,
        registry_actor_id: str | None = None,
        instance_id: str | None = None,
        worker_lease_seconds: int = 60,
        slot_lease_seconds: int = 60,
    ) -> None:
        if not callable(getattr(worker, "run_once", None)):
            raise TypeError("reviewer_worker_run_once_required")
        worker_id = getattr(worker, "worker_id", None)
        if not isinstance(worker_id, str) or not worker_id:
            raise ValueError("reviewer_worker_identity_required")
        if not callable(heartbeat_callback):
            raise TypeError("reviewer_heartbeat_callback_required")
        if type(max_concurrency) is not int or not 1 <= max_concurrency <= 32:
            raise ValueError("invalid_reviewer_worker_concurrency")
        if (isinstance(poll_interval_seconds, bool)
                or not isinstance(poll_interval_seconds, (int, float))
                or not 0.05 <= poll_interval_seconds <= 300):
            raise ValueError("invalid_reviewer_worker_poll_interval")
        self.worker = worker
        self.worker_id = worker_id
        self.heartbeat_callback = heartbeat_callback
        self.max_concurrency = max_concurrency
        if registry is not None and not isinstance(registry, ReviewerWorkerRegistry):
            raise TypeError("reviewer_worker_registry_required")
        if registry is not None and (not isinstance(registry_actor_id, str) or not registry_actor_id):
            raise ValueError("reviewer_worker_registry_actor_required")
        if type(worker_lease_seconds) is not int or not 10 <= worker_lease_seconds <= 900:
            raise ValueError("invalid_reviewer_worker_lease_ttl")
        if type(slot_lease_seconds) is not int or not 10 <= slot_lease_seconds <= 900:
            raise ValueError("invalid_reviewer_worker_slot_ttl")
        self.poll_interval_seconds = float(poll_interval_seconds)
        self.registry = registry
        self.registry_actor_id = registry_actor_id
        self.instance_id = instance_id
        self.worker_lease_seconds = worker_lease_seconds
        self.slot_lease_seconds = slot_lease_seconds
        self._registration = None
        self._stop = Event()
        self._running = False

    def _run_reserved(self, *, limit: int = 1):
        if self.registry is None:
            return self.worker.run_once(limit=limit)
        registration = self._registration
        if registration is None:
            raise ReviewerWorkerServiceError("worker_registry_not_registered")
        try:
            slot = self.registry.claim_slot(
                actor_id=self.registry_actor_id, worker_id=self.worker_id,
                instance_id=registration.instance_id, lease_token=registration.lease_token,
                max_concurrency=self.max_concurrency, slot_seconds=self.slot_lease_seconds,
            )
        except ReviewerWorkerRegistryConflict as exc:
            if str(exc) == "reviewer_global_concurrency_limit_reached":
                # Another process currently owns all globally configured slots.
                # Treat this as backpressure, not a worker execution failure.
                return SimpleNamespace(claimed=0, completed=0)
            raise
        finished = Event()
        renewal_errors = []

        def renew():
            interval = max(1.0, min(self.slot_lease_seconds, self.worker_lease_seconds) / 3)
            while not finished.wait(interval):
                try:
                    self.registry.heartbeat(
                        actor_id=self.registry_actor_id, worker_id=self.worker_id,
                        instance_id=registration.instance_id,
                        lease_token=registration.lease_token,
                        lease_seconds=self.worker_lease_seconds,
                    )
                    self.registry.renew_slot(
                        actor_id=self.registry_actor_id, worker_id=self.worker_id,
                        instance_id=registration.instance_id,
                        lease_token=registration.lease_token, slot_id=slot,
                        slot_seconds=self.slot_lease_seconds,
                    )
                except Exception as exc:
                    renewal_errors.append(type(exc).__name__)
                    finished.set()

        renewer = Thread(target=renew, name="yuki-reviewer-slot-renewal", daemon=True)
        renewer.start()
        try:
            report = self.worker.run_once(limit=limit)
            if renewal_errors:
                raise ReviewerWorkerServiceError("worker_slot_lease_renewal_failed")
            return report
        finally:
            finished.set()
            renewer.join(timeout=max(1.0, min(self.slot_lease_seconds, self.worker_lease_seconds) / 3 + 1))
            try:
                self.registry.release_slot(
                    actor_id=self.registry_actor_id, worker_id=self.worker_id,
                    instance_id=registration.instance_id,
                    lease_token=registration.lease_token, slot_id=slot,
                )
            except ReviewerWorkerRegistryConflict:
                # Expired/revoked slots are already unavailable to new work.
                pass

    def stop(self) -> None:
        """Request graceful shutdown; already-running executions are not killed."""
        self._stop.set()

    def _heartbeat(
        self, *, state: str, active_slots: int,
        completed: int, failures: int, last_error: str | None,
    ) -> None:
        self.heartbeat_callback(ReviewerWorkerHeartbeat(
            worker_id=self.worker_id,
            state=state,
            observed_at=datetime.now(timezone.utc).isoformat(),
            active_slots=active_slots,
            max_concurrency=self.max_concurrency,
            completed_dispatches=completed,
            failed_cycles=failures,
            last_error_code=last_error,
        ))

    def run_forever(self) -> ReviewerWorkerServiceReport:
        if self._running:
            raise ReviewerWorkerServiceError("reviewer_worker_service_already_running")
        self._running = True
        cycles = completed = failures = 0
        last_error = None
        try:
            if self.registry is not None:
                self._registration = self.registry.register(
                    actor_id=self.registry_actor_id, worker_id=self.worker_id,
                    instance_id=self.instance_id, lease_seconds=self.worker_lease_seconds,
                )
            with ThreadPoolExecutor(
                max_workers=self.max_concurrency,
                thread_name_prefix="yuki-reviewer",
            ) as pool:
                while not self._stop.is_set():
                    futures = [
                        pool.submit(self._run_reserved, limit=1)
                        for _ in range(self.max_concurrency)
                    ]
                    cycles += 1
                    done, _ = wait(futures, return_when=ALL_COMPLETED)
                    claimed_this_cycle = 0
                    for future in done:
                        try:
                            report = future.result()
                            claimed_this_cycle += int(getattr(report, "claimed", 0))
                            completed += int(getattr(report, "completed", 0))
                        except Exception as exc:
                            failures += 1
                            last_error = "worker_cycle." + type(exc).__name__
                    self._heartbeat(
                        state="stopping" if self._stop.is_set() else "running",
                        active_slots=0,
                        completed=completed,
                        failures=failures,
                        last_error=last_error,
                    )
                    if self.registry is not None and not self._stop.is_set():
                        self.registry.heartbeat(
                            actor_id=self.registry_actor_id, worker_id=self.worker_id,
                            instance_id=self._registration.instance_id,
                            lease_token=self._registration.lease_token,
                            lease_seconds=self.worker_lease_seconds,
                        )
                    if self._stop.is_set():
                        break
                    if claimed_this_cycle == 0:
                        self._stop.wait(self.poll_interval_seconds)
            self._heartbeat(
                state="stopped", active_slots=0,
                completed=completed, failures=failures,
                last_error=last_error,
            )
            return ReviewerWorkerServiceReport(
                worker_id=self.worker_id, cycles=cycles,
                completed_dispatches=completed, failed_cycles=failures,
                stopped=True,
            )
        finally:
            if self.registry is not None and self._registration is not None:
                try:
                    self.registry.stop(
                        actor_id=self.registry_actor_id, worker_id=self.worker_id,
                        instance_id=self._registration.instance_id,
                        lease_token=self._registration.lease_token,
                    )
                finally:
                    self._registration = None
            self._running = False


__all__ = [
    "ReviewerWorkerService", "ReviewerWorkerServiceError",
    "ReviewerWorkerHeartbeat", "ReviewerWorkerServiceReport",
]
