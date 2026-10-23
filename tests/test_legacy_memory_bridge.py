import json
import tempfile
import unittest
from pathlib import Path

from companion.memory import MemoryStore
from core.legacy_memory_bridge import LegacyMemoryBridgeError, bridge_legacy_memory


class LegacyMemoryBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.memory = self.root / "yuki_memory.json"
        self.learning = self.root / "yuki_learning.json"
        self.destination = self.root / "unified.json"

    def _write_sources(self):
        self.memory.write_text(json.dumps([
            {"id": "old-1", "created_at": "2026-01-01T00:00:00+00:00",
             "category": "preference", "text": "Prefer small releases"}
        ]), encoding="utf-8")
        self.learning.write_text(json.dumps([
            {"task": "deploy app", "outcome": "passed", "approach": "run tests",
             "lesson": "test before deploy", "created_at": "2026-01-02T00:00:00+00:00"}
        ]), encoding="utf-8")

    def test_preview_is_read_only_and_counts_both_sources(self):
        self._write_sources()
        memory_before = self.memory.read_bytes()
        learning_before = self.learning.read_bytes()
        result = bridge_legacy_memory(self.memory, self.learning, self.destination)
        self.assertFalse(result["applied"])
        self.assertEqual((result["memory_count"], result["learning_count"]), (1, 1))
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.memory.read_bytes(), memory_before)
        self.assertEqual(self.learning.read_bytes(), learning_before)

    def test_apply_keeps_sources_and_creates_backups_and_store_compatible_output(self):
        self._write_sources()
        originals = (self.memory.read_bytes(), self.learning.read_bytes())
        result = bridge_legacy_memory(self.memory, self.learning, self.destination, apply=True)
        self.assertTrue(result["applied"])
        self.assertEqual((self.memory.read_bytes(), self.learning.read_bytes()), originals)
        self.assertEqual(len(result["backups"]), 2)
        for backup, original in zip(result["backups"], originals):
            self.assertEqual(Path(backup).read_bytes(), original)
        store = MemoryStore(self.destination)
        entries = store.list(limit=50)
        self.assertEqual(len(entries), 2)
        learning = next(entry for entry in entries if entry["source"] == "legacy_learning")
        self.assertIn("test before deploy", learning["text"])
        self.assertEqual(learning["lesson"], "test before deploy")

    def test_secrets_are_redacted_from_imported_content(self):
        self.memory.write_text(json.dumps(["token: ghp_abcdefghijklmnopqrstuvwxyz123456"]), encoding="utf-8")
        self.learning.write_text(json.dumps([]), encoding="utf-8")
        bridge_legacy_memory(self.memory, self.learning, self.destination, apply=True)
        content = self.destination.read_text(encoding="utf-8")
        self.assertNotIn("ghp_abcdefghijklmnopqrstuvwxyz123456", content)
        self.assertIn("[REDACTED]", content)

    def test_malformed_sources_fail_closed_without_writing(self):
        self.memory.write_text("{broken", encoding="utf-8")
        self.learning.write_text("[]", encoding="utf-8")
        with self.assertRaises(LegacyMemoryBridgeError):
            bridge_legacy_memory(self.memory, self.learning, self.destination, apply=True)
        self.assertFalse(self.destination.exists())
        self.assertFalse(Path(str(self.memory) + ".pre-phase104.bak").exists())

    def test_destination_and_backup_collisions_are_refused(self):
        self._write_sources()
        self.destination.write_text("preserve", encoding="utf-8")
        with self.assertRaises(LegacyMemoryBridgeError):
            bridge_legacy_memory(self.memory, self.learning, self.destination, apply=True)
        self.assertEqual(self.destination.read_text(encoding="utf-8"), "preserve")
        self.destination.unlink()
        Path(str(self.memory) + ".pre-phase104.bak").write_text("keep", encoding="utf-8")
        with self.assertRaises(LegacyMemoryBridgeError):
            bridge_legacy_memory(self.memory, self.learning, self.destination, apply=True)
        self.assertFalse(self.destination.exists())

    def test_rejects_same_source_and_destination(self):
        self._write_sources()
        with self.assertRaises(LegacyMemoryBridgeError):
            bridge_legacy_memory(self.memory, self.memory, self.destination)


if __name__ == "__main__":
    unittest.main()
