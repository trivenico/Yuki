"""Tests for the reminder dispatcher using the real SQLite reminder store."""
import asyncio
from datetime import datetime, timedelta, timezone

from companion.reminder_dispatcher import ReminderDispatcher
from companion.reminder_store import CompanionReminderStore


def _store(tmp_path):
    return CompanionReminderStore(
        tmp_path / "reminders.sqlite3",
        authorize=lambda owner, action: owner == "owner-a" and action in {"create", "read", "cancel"},
    )


def test_dispatcher_delivers_and_acknowledges_with_stable_idempotency_key(tmp_path):
    store = _store(tmp_path)
    reminder = store.create(
        owner_id="owner-a", text="Review the deployment",
        due_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    received = []

    async def deliver(envelope):
        received.append(envelope)

    dispatcher = ReminderDispatcher(store, deliver, poll_interval_seconds=0.1)
    async def scenario():
        await dispatcher.start()
        for _ in range(30):
            if received:
                break
            await asyncio.sleep(0.02)
        await dispatcher.stop()

    asyncio.run(scenario())
    assert len(received) == 1
    assert received[0]["reminder_id"] == reminder["reminder_id"]
    assert received[0]["idempotency_key"] == reminder["reminder_id"]
    assert received[0]["owner_id"] == "owner-a"
    assert store.list(owner_id="owner-a", include_inactive=True)[0]["status"] == "delivered"


def test_dispatcher_records_delivery_failure_and_releases_lease(tmp_path):
    store = _store(tmp_path)
    store.create(
        owner_id="owner-a", text="Check the build",
        due_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )

    def deliver(_envelope):
        raise OSError("notification channel unavailable")

    dispatcher = ReminderDispatcher(store, deliver, poll_interval_seconds=0.1)
    asyncio.run(dispatcher._deliver_one(store.claim_due()[0]))
    row = store.list(owner_id="owner-a")[0]
    assert row["status"] == "pending"
    assert store.claim_due() == []




def test_dispatcher_does_not_prelease_queued_reminders(tmp_path):
    store = _store(tmp_path)
    first = store.create(
        owner_id="owner-a", text="Slow first delivery",
        due_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    second = store.create(
        owner_id="owner-a", text="Second delivery gets a fresh lease",
        due_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    received = []

    async def deliver(envelope):
        received.append(envelope["reminder_id"])
        if envelope["reminder_id"] == first["reminder_id"]:
            await asyncio.sleep(5.2)

    dispatcher = ReminderDispatcher(
        store, deliver, poll_interval_seconds=0.1, batch_size=2, lease_seconds=5,
    )

    async def scenario():
        await dispatcher.start()
        for _ in range(400):
            if len(received) == 2:
                break
            await asyncio.sleep(0.02)
        await dispatcher.stop()

    asyncio.run(scenario())
    assert received == [first["reminder_id"], second["reminder_id"]]
    assert store.list(owner_id="owner-a", include_inactive=True) == []

def test_dispatcher_skips_delivery_when_reminder_was_cancelled_after_claim(tmp_path):
    store = _store(tmp_path)
    reminder = store.create(
        owner_id="owner-a", text="Do not notify after cancellation",
        due_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    claim = store.claim_due()[0]
    received = []

    def deliver(envelope):
        received.append(envelope)

    assert store.cancel(owner_id="owner-a", reminder_id=reminder["reminder_id"])
    dispatcher = ReminderDispatcher(store, deliver)
    asyncio.run(dispatcher._deliver_one(claim))

    assert received == []
    assert store.list(owner_id="owner-a", include_inactive=True)[0]["status"] == "cancelled"


def test_dispatcher_rejects_invalid_configuration(tmp_path):
    store = _store(tmp_path)
    for kwargs in (
        {"poll_interval_seconds": 0},
        {"batch_size": 0},
        {"lease_seconds": 1},
    ):
        try:
            ReminderDispatcher(store, lambda _item: None, **kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid dispatcher configuration was accepted")
