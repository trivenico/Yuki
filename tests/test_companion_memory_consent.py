import tempfile
import unittest
from pathlib import Path

from companion.memory import MemoryStore
from companion.memory_consent import CompanionMemory, MemoryConsentError


class CompanionMemoryConsentTests(unittest.TestCase):
    def test_memory_is_disabled_by_default_and_writes_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = CompanionMemory(MemoryStore(Path(directory) / "memory.json"))
            self.assertFalse(manager.enabled)
            with self.assertRaises(MemoryConsentError):
                manager.remember("I prefer concise answers")
            self.assertFalse((Path(directory) / "memory.json").exists())

    def test_explicit_consent_is_required_for_remembering(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = CompanionMemory(MemoryStore(Path(directory) / "memory.json"))
            manager.enable()
            saved = manager.remember("I prefer concise answers", category="preference")
            self.assertEqual(saved["category"], "preference")
            self.assertEqual(manager.list_memories()[0]["text"], "I prefer concise answers")

    def test_disabling_stops_future_writes_but_keeps_existing_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = CompanionMemory(MemoryStore(Path(directory) / "memory.json"))
            manager.enable()
            saved = manager.remember("Use metric units")
            manager.disable()
            self.assertFalse(manager.enabled)
            self.assertEqual(manager.list_memories()[0]["id"], saved["id"])
            with self.assertRaises(MemoryConsentError):
                manager.remember("Another preference")

    def test_user_can_forget_one_memory_or_clear_all(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = CompanionMemory(MemoryStore(Path(directory) / "memory.json"))
            manager.enable()
            first = manager.remember("First")
            second = manager.remember("Second")
            self.assertTrue(manager.forget(first["id"]))
            self.assertFalse(manager.forget(first["id"]))
            self.assertEqual([item["id"] for item in manager.list_memories()], [second["id"]])
            self.assertEqual(manager.clear(), 1)
            self.assertEqual(manager.list_memories(), [])

    def test_consent_is_not_implicitly_restored_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.json"
            first_session = CompanionMemory(MemoryStore(path))
            first_session.enable()
            first_session.remember("Persisted with consent")
            restarted_session = CompanionMemory(MemoryStore(path))
            self.assertFalse(restarted_session.enabled)
            with self.assertRaises(MemoryConsentError):
                restarted_session.remember("No implicit consent")


if __name__ == "__main__":
    unittest.main()
