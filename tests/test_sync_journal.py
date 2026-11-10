import tempfile
import unittest
from pathlib import Path

from core.sync_journal import InvalidSyncEvent, SyncJournal, SyncJournalError


class SyncJournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.local = SyncJournal(str(self.root / "local.sqlite3"), "laptop-01")
        self.cloud = SyncJournal(str(self.root / "cloud.sqlite3"), "cloud-01")

    def tearDown(self):
        # Explicitly release SQLite handles before TemporaryDirectory cleanup,
        # particularly on Windows where open database files cannot be removed.
        self.local = None
        self.cloud = None

    def test_round_trip_events_and_cursor_based_incremental_export(self):
        first = self.local.append("memory.note.created", "note-1", {"text": "hello"})
        second = self.local.append("conversation.message", "conversation-1", {"text": "world"})
        self.assertEqual([first.sequence, second.sequence], [1, 2])
        self.assertEqual(self.cloud.ingest(self.local.export_after()), 2)
        self.assertEqual(self.cloud.cursors()["laptop-01"], 2)
        self.assertEqual(self.local.export_after(self.cloud.cursors()), [])

    def test_ingest_is_idempotent(self):
        event = self.local.append("memory.note.created", "note-1", {"text": "hello"})
        self.assertEqual(self.cloud.ingest([event]), 1)
        self.assertEqual(self.cloud.ingest([event]), 0)

    def test_duplicate_event_id_with_different_payload_is_rejected(self):
        event = self.local.append("memory.note.created", "note-1", {"text": "hello"})
        altered = {
            "event_id": event.event_id, "origin_id": event.origin_id,
            "sequence": event.sequence, "kind": event.kind, "entity_id": event.entity_id,
            "payload": {"text": "tampered"}, "created_at": event.created_at,
        }
        self.cloud.ingest([event])
        with self.assertRaises(SyncJournalError):
            self.cloud.ingest([altered])

    def test_conflicting_origin_sequence_is_rejected(self):
        first = self.local.append("memory.note.created", "note-1", {"text": "hello"})
        self.cloud.ingest([first])
        conflict = self.local.append("memory.note.created", "note-2", {"text": "another"})
        # Re-map the second event onto an already-used origin sequence with a new ID.
        mapped = {
            "event_id": "00000000-0000-4000-8000-000000000001",
            "origin_id": first.origin_id, "sequence": first.sequence,
            "kind": conflict.kind, "entity_id": conflict.entity_id,
            "payload": conflict.payload, "created_at": conflict.created_at,
        }
        with self.assertRaises(SyncJournalError):
            self.cloud.ingest([mapped])

    def test_rejects_invalid_events_and_oversized_batches(self):
        with self.assertRaises(InvalidSyncEvent):
            self.cloud.ingest([{"event_id": "bad"}])
        with self.assertRaises(InvalidSyncEvent):
            self.cloud.ingest([self.local.append("memory.note.created", "n", {"x": "y"})] * 2, limit=1)

    def test_stable_origin_is_bound_to_database(self):
        with self.assertRaises(SyncJournalError):
            SyncJournal(str(self.root / "local.sqlite3"), "other-origin")

    def test_cursor_validation_and_export_limits(self):
        with self.assertRaises(ValueError):
            self.local.export_after({"laptop-01": -1})
        with self.assertRaises(ValueError):
            self.local.export_after(limit=5001)


    def test_export_cursor_filters_each_origin_independently(self):
        self.local.append("memory.note.created", "local-1", {"text": "local"})
        self.cloud.append("memory.note.created", "cloud-1", {"text": "cloud"})
        self.local.ingest(self.cloud.export_after())
        exported = self.local.export_after({"laptop-01": 1, "cloud-01": 0})
        self.assertEqual([(event.origin_id, event.sequence) for event in exported], [("cloud-01", 1)])

    def test_concurrent_appends_allocate_unique_sequences(self):
        from concurrent.futures import ThreadPoolExecutor

        def append_one(index):
            return self.local.append("memory.note.created", f"note-{index}", {"index": index})

        with ThreadPoolExecutor(max_workers=8) as pool:
            events = list(pool.map(append_one, range(32)))
        self.assertEqual(sorted(event.sequence for event in events), list(range(1, 33)))

    def test_payload_must_be_bounded_json_object(self):
        with self.assertRaises(InvalidSyncEvent):
            self.local.append("memory.note.created", "n", {"data": object()})
        with self.assertRaises(InvalidSyncEvent):
            self.local.append("memory.note.created", "n", {"data": "x" * 70000})


if __name__ == "__main__":
    unittest.main()
