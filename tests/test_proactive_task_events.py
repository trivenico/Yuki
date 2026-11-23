from dataclasses import dataclass

from core.proactive_alerts import ProactiveAlertStore
from core.proactive_task_events import ProactiveTaskEventMonitor


@dataclass
class Summary:
    task_id: str


@dataclass
class Event:
    kind: str
    sequence: int
    payload: dict


class FakeTaskRuntime:
    def __init__(self, summaries, event_map):
        self.summaries = summaries
        self.event_map = event_map
        self.calls = []

    def list_task_summaries(self, *, actor_id, limit):
        self.calls.append(("list", actor_id, limit))
        return self.summaries[:limit]

    def recent_events(self, *, actor_id, task_id, limit):
        self.calls.append(("events", actor_id, task_id, limit))
        return self.event_map.get(task_id, [])[-limit:]


def test_task_event_monitor_creates_owner_scoped_alert_for_terminal_failure(tmp_path):
    runtime = FakeTaskRuntime(
        [Summary("task-1")],
        {"task-1": [
            Event("task.created", 1, {"name": "private task content"}),
            Event("task.failed", 2, {"error_code": "worker_error", "secret": "never expose"}),
        ]},
    )
    store = ProactiveAlertStore(tmp_path / "alerts.sqlite3")
    monitor = ProactiveTaskEventMonitor(
        runtime, store, notifications_enabled_for=lambda actor: actor == "owner-a",
    )
    report = monitor.run_once(actor_id="owner-a")
    assert report.scanned_tasks == 1
    assert report.scanned_events == 2
    assert report.alerts_created == 1
    assert report.notification_events_queued == 1
    alerts = store.recent(owner_id="owner-a")
    assert len(alerts) == 1
    assert alerts[0]["category"] == "task.failed"
    assert alerts[0]["subject"] == "task-1:2"
    assert "secret" not in alerts[0]["message"]
    assert runtime.calls[0] == ("list", "owner-a", 100)


def test_task_event_monitor_is_idempotent_across_repeated_scans(tmp_path):
    runtime = FakeTaskRuntime(
        [Summary("task-2")],
        {"task-2": [Event("task.failed", 4, {"error_code": "failed"})]},
    )
    store = ProactiveAlertStore(tmp_path / "alerts.sqlite3")
    monitor = ProactiveTaskEventMonitor(runtime, store, notifications_enabled_for=lambda _: True)
    first = monitor.run_once(actor_id="owner-a")
    second = monitor.run_once(actor_id="owner-a")
    assert first.notification_events_queued == 1
    assert second.notification_events_queued == 0
    history = store.notification_history(owner_id="owner-a")
    assert len(history) == 1
    assert store.recent(owner_id="owner-a")[0]["occurrence_count"] == 2


def test_task_event_monitor_never_alerts_for_progress_or_completion(tmp_path):
    runtime = FakeTaskRuntime(
        [Summary("task-3")],
        {"task-3": [
            Event("task.progress", 2, {"message": "working"}),
            Event("task.completed", 3, {"result": "sensitive result"}),
        ]},
    )
    store = ProactiveAlertStore(tmp_path / "alerts.sqlite3")
    monitor = ProactiveTaskEventMonitor(runtime, store, notifications_enabled_for=lambda _: True)
    report = monitor.run_once(actor_id="owner-a")
    assert report.scanned_events == 2
    assert report.alerts_created == 0
    assert store.recent(owner_id="owner-a") == []


def test_task_event_monitor_validates_actor_and_preference_resolver(tmp_path):
    import pytest
    runtime = FakeTaskRuntime([], {})
    store = ProactiveAlertStore(tmp_path / "alerts.sqlite3")
    monitor = ProactiveTaskEventMonitor(runtime, store, notifications_enabled_for=lambda _: 1)
    with pytest.raises(ValueError):
        monitor.run_once(actor_id="")
    with pytest.raises(ValueError, match="boolean"):
        monitor.run_once(actor_id="owner-a")
