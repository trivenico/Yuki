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

def test_zero_cooldown_creates_distinct_outbox_events_for_same_second(tmp_path):
    store = ProactiveAlertStore(tmp_path / "alerts.sqlite3")
    now = datetime(2026, 11, 23, 12, tzinfo=timezone.utc)
    first = store.observe(
        owner_id="owner-a", category="task.failed", subject="task-9",
        message="Task failed.", notifications_enabled=True,
        cooldown_seconds=0, now=now,
    )
    second = store.observe(
        owner_id="owner-a", category="task.failed", subject="task-9",
        message="Task still failed.", notifications_enabled=True,
        cooldown_seconds=0, now=now,
    )
    assert first["notification_id"] != second["notification_id"]
    assert len(store.notification_history(owner_id="owner-a")) == 2

def test_alert_acknowledgement_and_resolution_are_version_fenced_and_audited(tmp_path):
    store = ProactiveAlertStore(tmp_path / "alerts.sqlite3")
    observed = store.observe(
        owner_id="owner-a", category="task.failed", subject="task-ack",
        message="Task needs review.", notifications_enabled=False,
    )
    alert_id = observed["fingerprint"]
    acknowledged = store.acknowledge(
        owner_id="owner-a", alert_id=alert_id, actor_id="owner-a",
        expected_version=1,
    )
    assert acknowledged["status"] == "acknowledged"
    assert acknowledged["version"] == 2
    resolved = store.resolve(
        owner_id="owner-a", alert_id=alert_id, actor_id="owner-a",
        expected_version=2,
    )
    assert resolved["status"] == "resolved"
    assert resolved["version"] == 3
    alert = store.recent(owner_id="owner-a")[0]
    assert alert["status"] == "resolved"
    assert alert["version"] == 3
    audit = store.audit_history(owner_id="owner-a", alert_id=alert_id)
    assert [item["action"] for item in reversed(audit)] == ["acknowledged", "resolved"]
    assert all(item["actor_id"] == "owner-a" for item in audit)


def test_alert_lifecycle_rejects_other_owner_stale_version_and_invalid_transition(tmp_path):
    store = ProactiveAlertStore(tmp_path / "alerts.sqlite3")
    observed = store.observe(
        owner_id="owner-a", category="task.failed", subject="task-private",
        message="Private alert.", notifications_enabled=False,
    )
    alert_id = observed["fingerprint"]
    with pytest.raises(KeyError):
        store.acknowledge(
            owner_id="owner-b", alert_id=alert_id, actor_id="owner-b",
            expected_version=1,
        )
    store.acknowledge(
        owner_id="owner-a", alert_id=alert_id, actor_id="owner-a",
        expected_version=1,
    )
    with pytest.raises(ValueError, match="version_conflict"):
        store.resolve(
            owner_id="owner-a", alert_id=alert_id, actor_id="owner-a",
            expected_version=1,
        )
    store.resolve(
        owner_id="owner-a", alert_id=alert_id, actor_id="owner-a",
        expected_version=2,
    )
    with pytest.raises(ValueError, match="invalid_alert_transition"):
        store.acknowledge(
            owner_id="owner-a", alert_id=alert_id, actor_id="owner-a",
            expected_version=3,
        )


def test_existing_alert_database_migrates_lifecycle_columns(tmp_path):
    path = tmp_path / "alerts.sqlite3"
    original = ProactiveAlertStore(path)
    original.observe(
        owner_id="owner-a", category="task.failed", subject="legacy-alert",
        message="Existing alert.", notifications_enabled=False,
    )
    reopened = ProactiveAlertStore(path)
    alert = reopened.recent(owner_id="owner-a")[0]
    assert alert["status"] == "active"
    assert alert["version"] == 1
    assert alert["acknowledged_at"] is None
