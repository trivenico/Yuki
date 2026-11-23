from datetime import datetime, timedelta, timezone

import pytest

from core.proactive_alerts import ProactiveAlertStore


def test_alert_is_persisted_and_deduplicated_with_cooldown(tmp_path):
    path = tmp_path / "alerts.sqlite3"
    now = datetime(2026, 11, 23, 12, tzinfo=timezone.utc)
    store = ProactiveAlertStore(path)

    first = store.observe(
        owner_id="owner-a", category="task.failed", subject="task-17",
        message="A task failed.", notifications_enabled=True,
        cooldown_seconds=600, now=now,
    )
    assert first["notification_due"] is True
    assert first["occurrence_count"] == 1

    reopened = ProactiveAlertStore(path)
    repeated = reopened.observe(
        owner_id="owner-a", category="task.failed", subject="task-17",
        message="A task is still failed.", notifications_enabled=True,
        cooldown_seconds=600, now=now + timedelta(minutes=2),
    )
    assert repeated["notification_due"] is False
    assert repeated["occurrence_count"] == 2
    assert repeated["first_seen"] == first["first_seen"]

    after_cooldown = reopened.observe(
        owner_id="owner-a", category="task.failed", subject="task-17",
        message="A task remains failed.", notifications_enabled=True,
        cooldown_seconds=600, now=now + timedelta(minutes=11),
    )
    assert after_cooldown["notification_due"] is True
    assert after_cooldown["occurrence_count"] == 3


def test_notifications_disabled_still_records_without_notifying(tmp_path):
    store = ProactiveAlertStore(tmp_path / "alerts.sqlite3")
    result = store.observe(
        owner_id="owner-a", category="worker.stale", subject="worker-2",
        message="Worker is stale.", notifications_enabled=False,
    )
    assert result["notification_due"] is False
    assert store.recent(owner_id="owner-a")[0]["category"] == "worker.stale"


def test_alerts_are_isolated_by_owner_and_bounded_history(tmp_path):
    store = ProactiveAlertStore(tmp_path / "alerts.sqlite3")
    for index in range(505):
        store.observe(
            owner_id="owner-a", category="task.failed", subject=f"task-{index}",
            message="Task needs review.", notifications_enabled=False,
        )
    store.observe(
        owner_id="owner-b", category="task.failed", subject="task-1",
        message="Private to owner B.", notifications_enabled=False,
    )
    assert len(store.recent(owner_id="owner-a", limit=500)) == 500
    assert [item["message"] for item in store.recent(owner_id="owner-b")] == [
        "Private to owner B."
    ]


@pytest.mark.parametrize("kwargs", [
    {"category": "Task Failed"},
    {"subject": ""},
    {"message": "x" * 501},
    {"cooldown_seconds": -1},
    {"cooldown_seconds": 604801},
    {"notifications_enabled": 1},
])
def test_rejects_invalid_alert_metadata_and_policy(tmp_path, kwargs):
    store = ProactiveAlertStore(tmp_path / "alerts.sqlite3")
    values = {
        "owner_id": "owner-a", "category": "task.failed",
        "subject": "task-1", "message": "Task failed.",
        "notifications_enabled": True,
    }
    values.update(kwargs)
    with pytest.raises(ValueError):
        store.observe(**values)


def test_requires_timezone_aware_observation_time(tmp_path):
    store = ProactiveAlertStore(tmp_path / "alerts.sqlite3")
    with pytest.raises(ValueError):
        store.observe(
            owner_id="owner-a", category="task.failed", subject="task-1",
            message="Task failed.", notifications_enabled=True,
            now=datetime(2026, 11, 23, 12),
        )
