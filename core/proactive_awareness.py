"""Evidence-based JARVIS-style operational awareness.

This module combines already-authorized, read-only task and worker summaries.
It never performs polling, changes task state, or treats an observation as
permission to act. Callers remain responsible for authorization at each source.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import islice
from typing import Iterable

from core.awareness import AwarenessSnapshot


@dataclass(frozen=True)
class OperationalAwareness:
    snapshot: AwarenessSnapshot
    attention_items: tuple[str, ...]


def _timestamp(value: object, field: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError(f"{field} must be an ISO timestamp or None.")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO timestamp or None.") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone.")
    return parsed


def build_operational_awareness(
    *,
    current_goal: str,
    tasks: Iterable[object],
    workers: Iterable[object],
    now: datetime | None = None,
) -> OperationalAwareness:
    """Build a bounded snapshot from caller-authorized task and worker records.

    Task objects need only expose state, name, task_id, lease_expires_at.
    Worker objects need only expose status and worker_id. No task payload,
    result, worker metadata, credential, or raw event content is copied.
    """
    if not isinstance(current_goal, str) or len(current_goal) > 2000:
        raise ValueError("current_goal must be bounded text.")
    current = now or datetime.now(timezone.utc)
    if not isinstance(current, datetime) or current.tzinfo is None:
        raise ValueError("now must be a timezone-aware datetime.")
    if not isinstance(tasks, Iterable) or not isinstance(workers, Iterable):
        raise ValueError("tasks and workers must be iterable.")
    task_rows = list(islice(iter(tasks), 101))
    worker_rows = list(islice(iter(workers), 101))
    if len(task_rows) > 100 or len(worker_rows) > 100:
        raise ValueError("operational awareness is limited to 100 tasks and workers.")

    known: list[str] = []
    events: list[str] = []
    attention: list[str] = []
    active = queued = failed = 0
    expired_running = 0

    for task in task_rows:
        task_id = getattr(task, "task_id", None)
        name = getattr(task, "name", None)
        state = getattr(task, "state", None)
        if not all(isinstance(value, str) and value.strip() for value in (task_id, name, state)):
            raise ValueError("task summaries must include bounded task_id, name, and state.")
        if len(task_id) > 200 or len(name) > 200 or state not in {
            "queued", "waiting_approval", "running", "succeeded",
            "failed", "cancelled", "interrupted", "blocked",
        }:
            raise ValueError("task summary contains invalid metadata.")
        if state == "running":
            active += 1
            expires = _timestamp(getattr(task, "lease_expires_at", None), "lease_expires_at")
            if expires is not None and expires < current:
                expired_running += 1
                attention.append(f"Task {task_id} ({name}) has an expired worker lease.")
        elif state in {"queued", "waiting_approval"}:
            queued += 1
        elif state in {"failed", "interrupted", "blocked"}:
            failed += 1
            attention.append(f"Task {task_id} ({name}) is {state} and may need review.")

    stale_workers = 0
    for worker in worker_rows:
        worker_id = getattr(worker, "worker_id", None)
        status = getattr(worker, "status", None)
        if not isinstance(worker_id, str) or not worker_id.strip() or len(worker_id) > 200:
            raise ValueError("worker summaries must include a valid worker_id.")
        if status not in {"active", "stopping", "stopped", "stale"}:
            raise ValueError("worker summary contains an invalid status.")
        if status in {"stale", "stopped"}:
            stale_workers += 1
            attention.append(f"Worker {worker_id} is {status}.")

    known.extend((
        f"Authorized task snapshot: {len(task_rows)} tasks; {active} running; {queued} queued; {failed} failed, interrupted, or blocked.",
        f"Worker snapshot: {len(worker_rows)} workers; {stale_workers} stale or stopped.",
    ))
    if expired_running:
        events.append(f"{expired_running} running task(s) have expired leases.")
    if failed:
        events.append(f"{failed} task(s) need review.")
    if stale_workers:
        events.append(f"{stale_workers} worker(s) are not healthy.")
    uncertainty = "low" if task_rows or worker_rows else "unknown"
    if expired_running or stale_workers:
        uncertainty = "medium"

    return OperationalAwareness(
        snapshot=AwarenessSnapshot(
            mode="background",
            current_goal=current_goal.strip(),
            known_context=tuple(known),
            recent_events=tuple(events),
            uncertainty=uncertainty,
        ),
        attention_items=tuple(attention[:20]),
    )


class OperationalAwarenessService:
    """Compose existing authorized task and worker readers without owning them."""

    def __init__(self, task_runtime, worker_registry, *, authorize_worker_health):
        if not callable(getattr(task_runtime, "list_task_summaries", None)):
            raise TypeError("task_runtime must provide list_task_summaries().")
        if not callable(getattr(worker_registry, "list_workers", None)):
            raise TypeError("worker_registry must provide list_workers().")
        if not callable(authorize_worker_health):
            raise TypeError("authorize_worker_health must be callable.")
        self._tasks = task_runtime
        self._workers = worker_registry
        self._authorize_worker_health = authorize_worker_health

    def snapshot(self, *, actor_id: str, current_goal: str) -> OperationalAwareness:
        if not isinstance(actor_id, str) or not actor_id.strip() or len(actor_id) > 200:
            raise ValueError("actor_id must be non-empty bounded text.")
        if self._authorize_worker_health(actor_id) is not True:
            raise PermissionError("worker health awareness is not authorized.")
        # TaskRuntime independently enforces task:list and owner scoping.
        tasks = self._tasks.list_task_summaries(actor_id=actor_id, limit=100)
        workers = self._workers.list_workers(stale_after_seconds=60, limit=100)
        return build_operational_awareness(
            current_goal=current_goal, tasks=tasks, workers=workers,
        )
