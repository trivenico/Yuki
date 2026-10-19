import json
import tempfile
import unittest
from pathlib import Path

from core.companion_memory import CompanionMemory


class CompanionMemoryTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.store = CompanionMemory(Path(self.tempdir.name) / "memory.sqlite3")

    def test_requires_explicit_consent(self):
        with self.assertRaises(PermissionError):
            self.store.remember("preference", "likes concise answers")

    def test_remember_and_list(self):
        memory_id = self.store.remember(
            "preference", "likes concise answers", confirmed=True
        )
        items = self.store.list_memories()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], memory_id)
        self.assertEqual(items[0]["category"], "preference")

    def test_category_filter(self):
        self.store.remember("goal", "launch a shop", confirmed=True)
        self.store.remember("preference", "weekly planning", confirmed=True)
        self.assertEqual(len(self.store.list_memories("goal")), 1)

    def test_delete_memory(self):
        memory_id = self.store.remember("goal", "launch a shop", confirmed=True)
        self.assertTrue(self.store.delete_memory(memory_id))
        self.assertFalse(self.store.delete_memory(memory_id))
        self.assertEqual(self.store.list_memories(), [])

    def test_clear_requires_confirmation(self):
        self.store.remember("goal", "launch a shop", confirmed=True)
        with self.assertRaises(PermissionError):
            self.store.clear()
        self.assertEqual(self.store.clear(confirmed=True), 1)

    def test_export_is_valid_json(self):
        self.store.remember("goal", "launch a shop", confirmed=True)
        exported = json.loads(self.store.export_json())
        self.assertEqual(exported[0]["content"], "launch a shop")

    def test_rejects_empty_content(self):
        with self.assertRaises(ValueError):
            self.store.remember("goal", " ", confirmed=True)


if __name__ == "__main__":
    unittest.main()
