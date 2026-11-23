from datetime import datetime, timedelta, timezone

from core.proactive_alerts import ProactiveAlertStore
from core.proactive_notification_dispatcher import ProactiveNotificationDispatcher


def _record(store):
    return store.observe(
        owner_id="owner-a", category="task.failed", subject="task-42",
        message="Task failed and needs review.", notifications_enabled=True,
        now=datetime(2026, 11, 23, 12, tzinfo=timezone.utc),
    )


def test_observation_atomically_creates_owner_scoped_outbox_event(tmp_path):
    store = ProactiveAlertStore(tmp_path / "alerts.sqlite3")
    result = _record(store)
    assert result["notification_due"] is True
    assert result["notification_id"]
    history = store.notification_history(owner_id="owner-a")
    assert len(history) == 1
    assert history[0]["status"] == "pending"
    assert store.notification_history(owner_id="owner-b") == []


def test_dispatcher_delivers_and_records_auditable_outcome(tmp_path):
    store = ProactiveAlertStore(tmp_path / "alerts.sqlite3")
    _record(store)
    delivered = []
    dispatcher = ProactiveNotificationDispatcher(
        store, lambda event: delivered.append(event) or True,
    )
    result = dispatcher.dispatch_one(owner_id="owner-a")
    assert result.status == "delivered"
    assert len(delivered) == 1
    assert "lease_token" not in delivered[0]
    history = store.notification_history(owner_id="owner-a")
    assert history[0]["status"] == "delivered"
    assert history[0]["attempts"] == 1
    assert history[0]["delivered_at"]


def test_transport_failure_is_retryable_and_does_not_leak_exception_text(tmp_path):
    store = ProactiveAlertStore(tmp_path / "alerts.sqlite3")
    _record(store)

    def fail(_event):
        raise RuntimeError("contains secret token and private payload")

    dispatcher = ProactiveNotificationDispatcher(store, fail, max_attempts=2)
    first = dispatcher.dispatch_one(owner_id="owner-a")
    assert first.status == "retry_scheduled"
    history = store.notification_history(owner_id="owner-a")[0]
    assert history["status"] == "pending"
    assert history["last_error_type"] == "RuntimeError"
    assert "secret" not in str(history)

    second = dispatcher.dispatch_one(owner_id="owner-a")
    assert second.status == "retry_scheduled"
    history = store.notification_history(owner_id="owner-a")[0]
    assert history["status"] == "failed"
    assert history["attempts"] == 2


def test_claim_is_owner_scoped_and_expired_lease_can_be_recovered(tmp_path):
    store = ProactiveAlertStore(tmp_path / "alerts.sqlite3")
    now = datetime(2026, 11, 23, 12, tzinfo=timezone.utc)
    _record(store)
    first = store.claim_notification(owner_id="owner-a", lease_seconds=30, now=now)
    assert first is not None
    assert store.claim_notification(owner_id="owner-b", now=now) is None
    assert store.claim_notification(
        owner_id="owner-a", now=now + timedelta(seconds=31),
    ) is not None
    assert store.settle_notification(
        owner_id="owner-a", notification_id=first["notification_id"],
        lease_token=first["lease_token"], delivered=True,
        now=now + timedelta(seconds=32),
    ) is False


def test_only_current_claim_token_can_settle_notification(tmp_path):
    store = ProactiveAlertStore(tmp_path / "alerts.sqlite3")
    _record(store)
    claim = store.claim_notification(owner_id="owner-a")
    assert claim is not None
    assert store.settle_notification(
        owner_id="owner-a", notification_id=claim["notification_id"],
        lease_token="wrong-token", delivered=True,
    ) is False
    assert store.settle_notification(
        owner_id="owner-a", notification_id=claim["notification_id"],
        lease_token=claim["lease_token"], delivered=True,
    ) is True


def test_dispatcher_does_not_claim_when_no_pending_notification(tmp_path):
    store = ProactiveAlertStore(tmp_path / "alerts.sqlite3")
    dispatcher = ProactiveNotificationDispatcher(store, lambda _event: True)
    assert dispatcher.dispatch_one(owner_id="owner-a").status == "empty"
