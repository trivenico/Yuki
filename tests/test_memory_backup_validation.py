"""Strict schema and owner-binding tests for memory backup preview."""
from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone

from core.memory_backup_validation import (
    MemoryBackupValidationError,
    validate_memory_backup_snapshot,
)


def snapshot(owner="alice", memories=None):
    items = memories if memories is not None else [{
        "memory_id": "a" * 32,
        "category": "preference",
        "content": "Concise engineering updates",
        "created_at": "2026-11-16T10:00:00Z",
        "expires_at": None,
    }]
    return {
        "format": "yuki-memory-snapshot",
        "schema_version": 1,
        "owner_id": owner,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "memory_count": len(items),
        "memories": items,
    }


class MemoryBackupValidationTests(unittest.TestCase):
    def validate(self, value, owner="alice"):
        return validate_memory_backup_snapshot(
            json.dumps(value, separators=(",", ":")).encode("utf-8"), owner,
        )

    def test_valid_snapshot_returns_only_preview_metadata_and_ids(self):
        result = self.validate(snapshot())
        self.assertEqual(result["owner_id"], "alice")
        self.assertEqual(result["memory_count"], 1)
        self.assertEqual(result["category_counts"], {"lesson": 0, "note": 0, "preference": 1})
        self.assertEqual(result["memory_ids"], ("a" * 32,))

    def test_rejects_another_authenticated_owner(self):
        with self.assertRaisesRegex(MemoryBackupValidationError, "owner_mismatch"):
            self.validate(snapshot(owner="bob"))

    def test_rejects_unknown_snapshot_and_memory_fields(self):
        value = snapshot()
        value["unexpected"] = True
        with self.assertRaises(MemoryBackupValidationError):
            self.validate(value)
        value = snapshot()
        value["memories"][0]["unexpected"] = True
        with self.assertRaises(MemoryBackupValidationError):
            self.validate(value)

    def test_rejects_duplicate_ids_and_unexpected_record_owner_field(self):
        value = snapshot()
        value["memories"].append(dict(value["memories"][0]))
        value["memory_count"] = 2
        with self.assertRaisesRegex(MemoryBackupValidationError, "duplicate_memory_backup_id"):
            self.validate(value)
        value = snapshot()
        value["memories"][0]["owner_id"] = "bob"
        with self.assertRaises(MemoryBackupValidationError):
            self.validate(value)

    def test_rejects_bool_schema_version_wrong_counts_and_invalid_categories(self):
        value = snapshot()
        value["schema_version"] = True
        with self.assertRaises(MemoryBackupValidationError):
            self.validate(value)
        value = snapshot()
        value["memory_count"] = True
        with self.assertRaises(MemoryBackupValidationError):
            self.validate(value)
        value = snapshot()
        value["memories"][0]["category"] = "system"
        with self.assertRaises(MemoryBackupValidationError):
            self.validate(value)

    def test_rejects_invalid_timestamps_and_duplicate_json_keys(self):
        value = snapshot()
        value["memories"][0]["created_at"] = "not-a-date"
        with self.assertRaises(MemoryBackupValidationError):
            self.validate(value)
        duplicate = (
            b'{"format":"yuki-memory-snapshot","format":"other",'
            b'"schema_version":1,"owner_id":"alice","exported_at":"2026-11-16T10:00:00Z",'
            b'"memory_count":0,"memories":[]}'
        )
        with self.assertRaises(MemoryBackupValidationError):
            validate_memory_backup_snapshot(duplicate, "alice")

    def test_rejects_oversized_and_empty_payloads(self):
        for payload in (b"", b"x" * (8 * 1024 * 1024 + 1)):
            with self.subTest(size=len(payload)), self.assertRaises(MemoryBackupValidationError):
                validate_memory_backup_snapshot(payload, "alice")


if __name__ == "__main__":
    unittest.main()
