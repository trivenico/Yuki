"""One-shot bridge from authorized awareness snapshots to durable alerts.

The host scheduler may call run_once(). This service owns no thread, timer,
task retry, or notification transport.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from core.proactive_alerts import ProactiveAlertStore
from core.proactive_awareness import OperationalAwareness, OperationalAwarenessService


@dataclass(frozen=True)
class MonitoringReport:
    awareness: OperationalAwareness
    alerts: tuple[dict, ...]


class ProactiveMonitoringService:
    def __init__(
        self,
        awareness_service: OperationalAwarenessService,
        alert_store: ProactiveAlertStore,
        *,
        notifications_enabled_for,
        cooldown_seconds: int = 900,
    ):
        if not isinstance(awareness_service, OperationalAwarenessService):
            raise TypeError("awareness_service must be an OperationalAwarenessService.")
        if not isinstance(alert_store, ProactiveAlertStore):
            raise TypeError("alert_store must be a ProactiveAlertStore.")
        if not callable(notifications_enabled_for):
            raise TypeError("notifications_enabled_for must be callable.")
        if type(cooldown_seconds) is not int or not 0 <= cooldown_seconds <= 604800:
            raise ValueError("cooldown_seconds must be between 0 and 604800.")
        self._awareness = awareness_service
        self._alerts = alert_store
        self._notifications_enabled_for = notifications_enabled_for
        self._cooldown = cooldown_seconds

    @staticmethod
    def _identity(item: str) -> tuple[str, str]:
        task = re.match(r"Task ([^ ]+) ", item)
        worker = re.match(r"Worker ([^ ]+) ", item)
        if task:
            category = "task.expired_lease" if "expired worker lease" in item else "task.needs_review"
            return category, task.group(1)
        if worker:
            return "worker.unhealthy", worker.group(1)
        raise ValueError("awareness produced an unsupported attention item.")

    def run_once(self, *, actor_id: str, current_goal: str) -> MonitoringReport:
        """Take one authorized snapshot and update alert history; never delivers."""
        enabled = self._notifications_enabled_for(actor_id)
        if type(enabled) is not bool:
            raise ValueError("notification preference resolver must return boolean.")
        awareness = self._awareness.snapshot(
            actor_id=actor_id, current_goal=current_goal,
        )
        alerts = []
        for item in awareness.attention_items:
            category, subject = self._identity(item)
            alerts.append(self._alerts.observe(
                owner_id=actor_id,
                category=category,
                subject=subject,
                message=item,
                notifications_enabled=enabled,
                cooldown_seconds=self._cooldown,
            ))
        return MonitoringReport(awareness=awareness, alerts=tuple(alerts))
