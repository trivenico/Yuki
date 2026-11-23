import tempfile
import unittest
from pathlib import Path

from companion.memory import MemoryStore


class MemoryUsageTests(unittest.TestCase):
    def test_record_use_tracks_timestamp_and_count_without_mutating_text(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MemoryStore(Path(directory) / "memory.json")
            saved = store.add("A reviewed lesson.", category="lesson")
            used = store.record_use(saved["id"])
            self.assertIsNotNone(used)
            self.assertEqual(used["text"], "A reviewed lesson.")
            self.assertEqual(used["use_count"], 1)
            self.assertTrue(used["last_used_at"])
            used_again = store.record_use(saved["id"])
            self.assertEqual(used_again["use_count"], 2)

    def test_record_use_returns_none_for_unknown_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MemoryStore(Path(directory) / "memory.json")
            self.assertIsNone(store.record_use("missing"))


if __name__ == "__main__":
    unittest.main()
