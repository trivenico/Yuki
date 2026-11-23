"""Owner-scoped bridge from durable task lifecycle events to proactive alerts.

This adapter polls the bounded task journal on demand or from a host-owned
scheduler. It never executes, retries, cancels, or modifies tasks.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.proactive_alerts import ProactiveAlertStore


_EVENT_CATEGORIES = {
    "task.failed": "task.failed",
    "task.blocked": "task.blocked",
    "task.interrupted": "task.interrupted",
}


@dataclass(frozen=True)
class TaskEventMonitoringReport:
    scanned_tasks: int
    scanned_events: int
    alerts_created: int
    notification_events_queued: int


class ProactiveTaskEventMonitor:
    """Turn allowlisted terminal task events into durable, owner-scoped alerts."""

    def __init__(
        self, task_runtime, alert_store: ProactiveAlertStore, *,
        notifications_enabled_for, cooldown_seconds: int = 900,
        task_limit: int = 100, events_per_task: int = 100,
    ):
        if not callable(getattr(task_runtime, "list_task_summaries", None)) or not callable(
            getattr(task_runtime, "recent_events", None)
        ):
            raise TypeError("task_runtime must provide list_task_summaries() and recent_events().")
        if not isinstance(alert_store, ProactiveAlertStore):
            raise TypeError("alert_store must be a ProactiveAlertStore.")
        if not callable(notifications_enabled_for):
            raise TypeError("notifications_enabled_for must be callable.")
        if type(cooldown_seconds) is not int or not 0 <= cooldown_seconds <= 604800:
            raise ValueError("cooldown_seconds must be between 0 and 604800.")
        if type(task_limit) is not int or not 1 <= task_limit <= 100:
            raise ValueError("task_limit must be between 1 and 100.")
        if type(events_per_task) is not int or not 1 <= events_per_task <= 500:
            raise ValueError("events_per_task must be between 1 and 500.")
        self._tasks = task_runtime
        self._alerts = alert_store
        self._preferences = notifications_enabled_for
        self._cooldown = cooldown_seconds
        self._task_limit = task_limit
        self._events_per_task = events_per_task

    @staticmethod
    def _actor(actor_id: str) -> str:
        if not isinstance(actor_id, str) or not actor_id.strip() or len(actor_id) > 200 or "\x00" in actor_id:
            raise ValueError("actor_id must be non-empty bounded text.")
        return actor_id.strip()

    def run_once(self, *, actor_id: str) -> TaskEventMonitoringReport:
        """Scan recent owner-authorized events and record only allowlisted outcomes."""
        actor = self._actor(actor_id)
        enabled = self._preferences(actor)
        if type(enabled) is not bool:
            raise ValueError("notification preference resolver must return boolean.")
        tasks = self._tasks.list_task_summaries(actor_id=actor, limit=self._task_limit)
        scanned_events = 0
        alerts_created = 0
        queued = 0
        for task in tasks:
            task_id = getattr(task, "task_id", None)
            if not isinstance(task_id, str) or not task_id or len(task_id) > 200 or "\x00" in task_id:
                continue
            events = self._tasks.recent_events(
                actor_id=actor, task_id=task_id, limit=self._events_per_task,
            )
            for event in events:
                scanned_events += 1
                category = _EVENT_CATEGORIES.get(getattr(event, "kind", None))
                sequence = getattr(event, "sequence", None)
                if category is None or type(sequence) is not int or sequence < 1:
                    continue
                subject = f"{task_id}:{sequence}"
                result = self._alerts.observe(
                    owner_id=actor,
                    category=category,
                    subject=subject,
                    message=f"Task {task_id} {category.removeprefix('task.')} (event {sequence}). Review its task history.",
                    notifications_enabled=enabled,
                    cooldown_seconds=self._cooldown,
                )
                alerts_created += 1
                queued += int(result["notification_due"])
        return TaskEventMonitoringReport(
            scanned_tasks=len(tasks), scanned_events=scanned_events,
            alerts_created=alerts_created, notification_events_queued=queued,
        )
