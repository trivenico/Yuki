"""Bounded, deterministic operational overview for Yuki's supervision UI.

This service aggregates metadata only. It never reads task results, executes
work, mutates alerts, or grants access to the global worker registry.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Any


@dataclass(frozen=True)
class OperationalOverview:
    generated_at: str
    task_access: bool
    worker_access: bool
    task_counts: dict[str, int]
    worker_counts: dict[str, int]
    alert_counts: dict[str, int]
    expired_running_leases: int
    recent_alerts: tuple[dict[str, Any], ...]

    def render(self) -> str:
        lines = [
            "Yuki Operational Intelligence Center (read-only)",
            f"Generated: {self.generated_at}",
        ]
        if self.task_access:
            lines.append(
                "Tasks: " + ", ".join(
                    f"{key}={self.task_counts.get(key, 0)}"
                    for key in ("total", "queued", "waiting_approval", "running", "succeeded", "failed", "cancelled", "interrupted", "blocked", "other")
                )
            )
            lines.append(f"Running tasks with expired leases: {self.expired_running_leases}")
        else:
            lines.append("Tasks: unavailable (task:list permission or runtime unavailable)")
        if self.worker_access:
            lines.append("Workers: " + ", ".join(
                f"{key}={self.worker_counts.get(key, 0)}"
                for key in ("total", "active", "stopping", "stopped", "stale", "other")
            ))
        else:
            lines.append("Workers: unavailable (operator health access not granted)")
        lines.append("Alerts: " + ", ".join(
            f"{key}={self.alert_counts.get(key, 0)}"
            for key in ("total", "active", "acknowledged", "resolved")
        ))
        if self.recent_alerts:
            lines.append("Recent alerts:")
            for alert in self.recent_alerts:
                lines.append(
                    f"- {alert.get('category', 'unknown')} | "
                    f"{alert.get('subject', 'unknown')} | "
                    f"{alert.get('status', 'active')} | "
                    f"v{alert.get('version', 1)}"
                )
        else:
            lines.append("Recent alerts: none")
        lines.append("No tasks were started, retried, cancelled, or modified.")
        return "\n".join(lines)


class OperationalIntelligenceCenter:
    """Compose owner-authorized task/alert metadata and gated worker health."""

    def __init__(
        self, *, task_runtime, worker_registry, alert_store,
        worker_access_for: Callable[[str], bool],
        task_limit: int = 100, alert_limit: int = 10,
    ):
        if not callable(getattr(task_runtime, "list_task_summaries", None)):
            raise TypeError("task_runtime must provide list_task_summaries().")
        if not callable(getattr(worker_registry, "list_workers", None)):
            raise TypeError("worker_registry must provide list_workers().")
        if not callable(getattr(alert_store, "recent", None)):
            raise TypeError("alert_store must provide recent().")
        if not callable(worker_access_for):
            raise TypeError("worker_access_for must be callable.")
        if type(task_limit) is not int or not 1 <= task_limit <= 500:
            raise ValueError("task_limit must be from 1 to 500.")
        if type(alert_limit) is not int or not 1 <= alert_limit <= 50:
            raise ValueError("alert_limit must be from 1 to 50.")
        self.task_runtime = task_runtime
        self.worker_registry = worker_registry
        self.alert_store = alert_store
        self.worker_access_for = worker_access_for
        self.task_limit = task_limit
        self.alert_limit = alert_limit

    def snapshot(self, *, actor_id: str) -> OperationalOverview:
        if not isinstance(actor_id, str) or not actor_id.strip() or len(actor_id) > 200 or "\x00" in actor_id:
            raise ValueError("actor_id must be non-empty bounded text.")
        actor = actor_id.strip()
        now = datetime.now(timezone.utc)
        task_counts: Counter[str] = Counter()
        worker_counts: Counter[str] = Counter()
        alert_counts: Counter[str] = Counter()
        expired_running = 0
        task_access = False
        worker_access = False

        try:
            tasks = self.task_runtime.list_task_summaries(
                actor_id=actor, limit=self.task_limit,
            )
            task_access = True
            task_counts["total"] = len(tasks)
            for task in tasks:
                state = task.state if task.state in {
                    "queued", "waiting_approval", "running", "succeeded", "failed", "cancelled", "interrupted", "blocked",
                } else "other"
                task_counts[state] += 1
                if task.state == "running" and task.lease_expires_at:
                    try:
                        expiry = datetime.fromisoformat(task.lease_expires_at)
                        if expiry.tzinfo is not None and expiry <= now:
                            expired_running += 1
                    except (TypeError, ValueError):
                        pass
        except Exception:
            pass

        try:
            allowed = self.worker_access_for(actor)
        except Exception:
            allowed = False
        if allowed is True:
            try:
                workers = self.worker_registry.list_workers(stale_after_seconds=60, limit=200)
                worker_access = True
                worker_counts["total"] = len(workers)
                for worker in workers:
                    status = worker.status if worker.status in {
                        "active", "stopping", "stopped", "stale",
                    } else "other"
                    worker_counts[status] += 1
            except Exception:
                pass

        alerts = self.alert_store.recent(owner_id=actor, limit=self.alert_limit)
        alert_counts["total"] = len(alerts)
        for alert in alerts:
            status = alert.get("status", "active")
            if status not in {"active", "acknowledged", "resolved"}:
                status = "other"
            alert_counts[status] += 1

        return OperationalOverview(
            generated_at=now.isoformat(timespec="seconds"),
            task_access=task_access,
            worker_access=worker_access,
            task_counts=dict(task_counts),
            worker_counts=dict(worker_counts),
            alert_counts=dict(alert_counts),
            expired_running_leases=expired_running,
            recent_alerts=tuple(alerts),
        )
