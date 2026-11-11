import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from companion.notification_dispatcher import NotificationDispatcher
from companion.proactive import ReminderQueue
from companion.state_store import CompanionStateStore


class _RecordingSink:
    def __init__(self, *, fail=False):
        self.deliveries = []
        self.fail = fail

    def deliver(self, notification, *, idempotency_key, timeout_seconds):
        self.deliveries.append((notification, idempotency_key, timeout_seconds))
        if self.fail:
            raise RuntimeError("provider error included secret=do-not-persist")


class NotificationDispatcherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = CompanionStateStore(self.root / "state.sqlite3", owner_id="owner")
        self.reminders = ReminderQueue(state_store=self.store)
        self.now = datetime.now(timezone.utc)
        self.reminders.add("Check Yuki", self.now - timedelta(seconds=1))

    def tearDown(self):
        self.temp.cleanup()

    def test_dispatch_delivers_and_marks_reminder_only_after_success(self):
        sink = _RecordingSink()
        dispatcher = NotificationDispatcher(self.reminders, sink)
        result = dispatcher.dispatch_once(self.now)
        self.assertEqual((result.claimed, result.delivered, result.retry_scheduled, result.lease_lost),
                         (1, 1, 0, 0))
        self.assertEqual(sink.deliveries[0][0]["text"], "Check Yuki")
        self.assertEqual(sink.deliveries[0][1], "reminder:" + self.reminders.list()[0]["id"])
        self.assertGreaterEqual(sink.deliveries[0][2], 1)
        self.assertTrue(self.reminders.list()[0]["delivered"])

    def test_dispatch_schedules_retry_and_suppresses_provider_error_text(self):
        sink = _RecordingSink(fail=True)
        dispatcher = NotificationDispatcher(self.reminders, sink, retry_after_seconds=30)
        result = dispatcher.dispatch_once(self.now)
        self.assertEqual((result.claimed, result.delivered, result.retry_scheduled), (1, 0, 1))
        self.assertFalse(self.reminders.list()[0]["delivered"])
        event = self.store.list_outbox()[0]
        self.assertEqual(event["status"], "pending")
        self.assertEqual(event["last_error"], "RuntimeError")
        self.assertNotIn("do-not-persist", event["last_error"])

    def test_dispatch_is_bounded_and_leaves_later_events_for_next_pass(self):
        self.reminders.add("Second reminder", self.now - timedelta(seconds=1))
        sink = _RecordingSink()
        dispatcher = NotificationDispatcher(self.reminders, sink)
        result = dispatcher.dispatch_once(self.now, limit=1)
        self.assertEqual(result.claimed, 1)
        self.assertEqual(result.delivered, 1)
        self.assertEqual(len(sink.deliveries), 1)
        next_result = dispatcher.dispatch_once(self.now + timedelta(seconds=1), limit=1)
        self.assertEqual(next_result.claimed, 1)

    def test_rejects_invalid_sink_and_retry_bounds(self):
        with self.assertRaises(TypeError):
            NotificationDispatcher(self.reminders, object())
        with self.assertRaises(ValueError):
            NotificationDispatcher(self.reminders, _RecordingSink(), retry_after_seconds=0)


if __name__ == "__main__":
    unittest.main()
