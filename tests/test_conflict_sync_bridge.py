"""Tests for privacy-controlled conflict journal publication."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.conflict_ledger import ConflictRecord
from core.conflict_sync_bridge import (
    EVENT_KIND,
    ConflictSyncError,
    conflict_event_payload,
    publish_conflict,
)
from core.sync_journal import SyncJournal


class ConflictSyncBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.conflict = ConflictRecord(
            conflict_id="conflict-abc",
            memory_id="memory-123",
            versions=(
                {"memory_id": "memory-123", "version_id": "v1", "origin_id": "laptop",
                 "sequence": 1, "text": "private local note", "category": "note",
                 "parent_version_id": None},
                {"memory_id": "memory-123", "version_id": "v2", "origin_id": "cloud",
                 "sequence": 2, "text": "private cloud note", "category": "preference",
                 "parent_version_id": None},
            ),
            status="open",
            created_at="2026-10-23T00:00:00+00:00",
        )
        self.journal = SyncJournal(str(self.root / "journal.sqlite"), "laptop")

    def tearDown(self):
        self.temp.cleanup()

    def test_requires_explicit_sync_opt_in(self):
        with self.assertRaises(ConflictSyncError):
            publish_conflict(self.journal, self.conflict)
        self.assertEqual(self.journal.export_after(), [])

    def test_metadata_only_default_does_not_include_memory_text(self):
        payload = conflict_event_payload(self.conflict)
        self.assertEqual(payload["schema_version"], 1)
        self.assertFalse(payload["contains_memory_text"])
        self.assertEqual(payload["conflict_id"], "conflict-abc")
        self.assertEqual([v["version_id"] for v in payload["candidates"]], ["v1", "v2"])
        self.assertNotIn("text", payload["candidates"][0])

    def test_text_included_only_with_second_explicit_opt_in(self):
        payload = conflict_event_payload(self.conflict, share_memory_text=True)
        self.assertTrue(payload["contains_memory_text"])
        self.assertEqual(payload["candidates"][0]["text"], "private local note")

    def test_publish_appends_versioned_event(self):
        event = publish_conflict(self.journal, self.conflict, allow_conflict_sync=True)
        self.assertEqual(event.kind, EVENT_KIND)
        self.assertEqual(event.entity_id, "conflict-abc")
        self.assertEqual(event.payload["schema_version"], 1)
        self.assertFalse(event.payload["contains_memory_text"])
        self.assertEqual(self.journal.export_after()[0], event)

    def test_invalid_candidate_sequence_rejected(self):
        bad = ConflictRecord(
            conflict_id="conflict-bad", memory_id="memory-123",
            versions=(
                {"version_id": "v1", "origin_id": "laptop", "sequence": True},
                {"version_id": "v2", "origin_id": "cloud", "sequence": 2},
            ), status="open", created_at="now",
        )
        with self.assertRaises(ConflictSyncError):
            conflict_event_payload(bad)

    def test_oversized_memory_text_rejected_when_sharing(self):
        versions = tuple(dict(v, text="x" * 4001) for v in self.conflict.versions)
        bad = ConflictRecord(
            conflict_id="conflict-large", memory_id="memory-123", versions=versions,
            status="open", created_at="now",
        )
        with self.assertRaises(ConflictSyncError):
            conflict_event_payload(bad, share_memory_text=True)


if __name__ == "__main__":
    unittest.main()
