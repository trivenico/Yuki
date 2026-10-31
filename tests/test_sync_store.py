import tempfile
import unittest
from pathlib import Path

from core.sync_store import (
    RevokedDeviceError, SyncConflictError, SyncEventStore, SyncStoreError,
)


def event(event_id="event_00000001", device="device_0000001", revision=1, payload=None):
    return {
        "event_id": event_id, "device_id": device,
        "collection": "task_records", "object_id": "task_00000001",
        "revision": revision, "occurred_at": "2026-10-31T10:00:00Z",
        "operation": "upsert", "payload": payload or {"status": "open"},
    }


class SyncEventStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = SyncEventStore(Path(self.temp.name) / "sync.sqlite3")
        self.store.register_device("device_0000001", "user_0000001")
        self.store.register_device("device_0000002", "user_0000001")

    def tearDown(self):
        self.temp.cleanup()

    def test_append_and_pull_in_sequence_order(self):
        stored = self.store.append_event("device_0000001", event())
        self.assertEqual(stored.sequence, 1)
        pulled = self.store.pull_events("device_0000002")
        self.assertEqual([item.event.event_id for item in pulled], ["event_00000001"])

    def test_idempotent_retry_returns_original_sequence(self):
        first = self.store.append_event("device_0000001", event())
        retry = self.store.append_event("device_0000001", event())
        self.assertEqual(first.sequence, retry.sequence)
        self.assertEqual(self.store.health()["events"], 1)

    def test_reused_event_id_with_different_content_is_rejected(self):
        self.store.append_event("device_0000001", event())
        with self.assertRaises(SyncStoreError):
            self.store.append_event("device_0000001", event(payload={"status": "changed"}))

    def test_rejects_revision_gap_and_stale_write(self):
        with self.assertRaises(SyncConflictError):
            self.store.append_event("device_0000001", event(revision=2))
        self.store.append_event("device_0000001", event())
        with self.assertRaises(SyncConflictError):
            self.store.append_event("device_0000002", event("event_00000002", "device_0000002", revision=1))

    def test_device_identity_is_bound_to_authenticated_device(self):
        with self.assertRaises(SyncStoreError):
            self.store.append_event("device_0000001", event(device="device_0000002"))

    def test_devices_cannot_read_another_users_events(self):
        self.store.register_device("device_0000003", "user_0000002")
        self.store.append_event("device_0000001", event())
        self.assertEqual(self.store.pull_events("device_0000003"), [])

    def test_acknowledgement_is_monotonic_and_bounded(self):
        self.store.append_event("device_0000001", event())
        self.assertEqual(self.store.acknowledge("device_0000002", 1), 1)
        with self.assertRaises(ValueError):
            self.store.acknowledge("device_0000002", 0)
        with self.assertRaises(ValueError):
            self.store.acknowledge("device_0000002", 9)

    def test_revoked_devices_cannot_write_or_read(self):
        self.store.revoke_device("device_0000001", "user_0000001")
        with self.assertRaises(RevokedDeviceError):
            self.store.append_event("device_0000001", event())
        with self.assertRaises(RevokedDeviceError):
            self.store.pull_events("device_0000001")

    def test_shared_in_memory_database_survives_per_operation_connections(self):
        memory = SyncEventStore(":memory:")
        try:
            memory.register_device("device_0000009", "user_0000009")
            stored = memory.append_event(
                "device_0000009",
                event("event_00000009", "device_0000009"),
            )
            self.assertEqual(stored.sequence, 1)
            self.assertEqual(len(memory.pull_events("device_0000009")), 1)
            self.assertTrue(memory.health()["ok"])
        finally:
            memory.close()

    def test_close_is_safe_for_file_backed_store(self):
        self.store.close()
        self.assertTrue(self.store.health()["ok"])

    def test_health_reports_sqlite_integrity(self):
        self.assertTrue(self.store.health()["ok"])


if __name__ == "__main__":
    unittest.main()
