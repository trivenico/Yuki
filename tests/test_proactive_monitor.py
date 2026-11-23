from dataclasses import dataclass

from core.proactive_alerts import ProactiveAlertStore
from core.proactive_awareness import OperationalAwarenessService
from core.proactive_monitor import ProactiveMonitoringService


@dataclass
class Task:
    task_id: str
    name: str
    state: str
    lease_expires_at: str | None = None


class Tasks:
    def list_task_summaries(self, *, actor_id, limit):
        return [Task("task-9", "Build", "failed")]


class Workers:
    def list_workers(self, *, stale_after_seconds, limit):
        return []


def test_one_shot_monitoring_connects_authorized_awareness_to_alert_store(tmp_path):
    awareness = OperationalAwarenessService(
        Tasks(), Workers(), authorize_worker_health=lambda actor: actor == "owner-a",
    )
    store = ProactiveAlertStore(tmp_path / "alerts.sqlite3")
    service = ProactiveMonitoringService(
        awareness, store, notifications_enabled_for=lambda actor: True,
        cooldown_seconds=300,
    )

    first = service.run_once(actor_id="owner-a", current_goal="Finish build")
    assert len(first.alerts) == 1
    assert first.alerts[0]["notification_due"] is True
    assert first.alerts[0]["category"] == "task.needs_review"
    assert first.alerts[0]["subject"] == "task-9"

    second = service.run_once(actor_id="owner-a", current_goal="Finish build")
    assert second.alerts[0]["notification_due"] is False
    assert second.alerts[0]["occurrence_count"] == 2
    assert len(store.recent(owner_id="owner-a")) == 1


def test_monitoring_fails_closed_before_reading_or_recording_when_unauthorized(tmp_path):
    awareness = OperationalAwarenessService(
        Tasks(), Workers(), authorize_worker_health=lambda actor: False,
    )
    store = ProactiveAlertStore(tmp_path / "alerts.sqlite3")
    service = ProactiveMonitoringService(
        awareness, store, notifications_enabled_for=lambda actor: True,
    )
    import pytest
    with pytest.raises(PermissionError):
        service.run_once(actor_id="owner-a", current_goal="")
    assert store.recent(owner_id="owner-a") == []


def test_disabled_notification_preference_records_history_without_due_alert(tmp_path):
    awareness = OperationalAwarenessService(
        Tasks(), Workers(), authorize_worker_health=lambda actor: True,
    )
    store = ProactiveAlertStore(tmp_path / "alerts.sqlite3")
    service = ProactiveMonitoringService(
        awareness, store, notifications_enabled_for=lambda actor: False,
    )
    report = service.run_once(actor_id="owner-a", current_goal="")
    assert report.alerts[0]["notification_due"] is False
    assert len(store.recent(owner_id="owner-a")) == 1
