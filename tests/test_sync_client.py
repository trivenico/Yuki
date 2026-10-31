import tempfile
import unittest
from pathlib import Path

from core.sync_client import SyncClientError, SyncOutbox, SyncHTTPTransport


def make_event(event_id="event_00000001", revision=1):
    return {
        "event_id": event_id, "device_id": "device_0000001",
        "collection": "task_records", "object_id": "task_00000001",
        "revision": revision, "occurred_at": "2026-10-31T10:00:00Z",
        "operation": "upsert", "payload": {"status": "open"},
    }


class SyncOutboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.outbox = SyncOutbox(Path(self.tmp.name) / "outbox.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def test_enqueue_is_durable_and_idempotent(self):
        self.assertTrue(self.outbox.enqueue(make_event()))
        self.assertFalse(self.outbox.enqueue(make_event()))
        self.assertEqual(self.outbox.ready(), [make_event()])

    def test_event_id_reuse_with_different_payload_is_rejected(self):
        self.outbox.enqueue(make_event())
        changed = make_event()
        changed["payload"] = {"status": "closed"}
        with self.assertRaises(SyncClientError):
            self.outbox.enqueue(changed)

    def test_retry_uses_bounded_exponential_backoff(self):
        self.outbox.enqueue(make_event())
        self.outbox.mark_retry("event_00000001", "temporary", now=1000)
        self.assertEqual(self.outbox.ready(now=1001), [])
        self.assertEqual(self.outbox.ready(now=1005), [make_event()])

    def test_conflicts_are_quarantined_not_retried(self):
        self.outbox.enqueue(make_event())
        self.outbox.mark_conflict("event_00000001", "revision mismatch")
        self.assertEqual(self.outbox.ready(), [])
        self.assertEqual(self.outbox.counts()["conflict"], 1)

    def test_cursor_is_monotonic(self):
        self.outbox.set_cursor(12)
        self.assertEqual(self.outbox.cursor(), 12)
        with self.assertRaises(ValueError):
            self.outbox.set_cursor(11)

    def test_memory_outbox_survives_separate_connections(self):
        memory = SyncOutbox(":memory:")
        try:
            self.assertTrue(memory.enqueue(make_event()))
            self.assertEqual(memory.ready(), [make_event()])
        finally:
            memory.close()

    def test_transport_rejects_non_https_endpoints(self):
        with self.assertRaises(ValueError):
            SyncHTTPTransport("http://sync.example.com")


if __name__ == "__main__":
    unittest.main()
