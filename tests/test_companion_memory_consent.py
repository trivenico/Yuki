import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from companion.memory import MemoryStore
from companion.memory_consent import CompanionMemory, MemoryConsentError


class CompanionMemoryConsentTests(unittest.TestCase):
    def test_encrypted_memory_can_be_selected_explicitly(self):
        class FakeEncryptedStore(MemoryStore):
            pass

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.enc.json"
            with patch.dict("os.environ", {"YUKI_MEMORY_ENCRYPTION": "1", "YUKI_MEMORY_PATH": str(path)}):
                with patch("companion.secure_memory.EncryptedMemoryStore", FakeEncryptedStore):
                    manager = CompanionMemory()
            self.assertIsInstance(manager._store, FakeEncryptedStore)
            self.assertEqual(manager._store.path, path.resolve())

    def test_saved_memory_context_requires_separate_session_consent(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = CompanionMemory(MemoryStore(Path(directory) / "memory.json"))
            manager.enable()
            manager.remember("Prefer concise answers", category="preference")
            self.assertFalse(manager.context_enabled)
            self.assertEqual(manager.context_entries(), [])
            manager.enable_context()
            self.assertTrue(manager.context_enabled)
            self.assertEqual(manager.context_entries()[0]["text"], "Prefer concise answers")
            manager.disable_context()
            self.assertEqual(manager.context_entries(), [])
            manager.enable_context()
            manager.disable()
            self.assertFalse(manager.context_enabled)
            self.assertEqual(manager.context_entries(), [])

    def test_context_retrieval_prefers_relevant_memories_and_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = CompanionMemory(MemoryStore(Path(directory) / "memory.json"))
            manager.enable()
            manager.remember("I prefer concise answers", category="preference")
            manager.remember("My favorite programming language is Python")
            manager.remember("I am planning a trip to Japan")
            manager.enable_context()
            results = manager.context_entries(query="Can you explain Python programming?", limit=2)
            self.assertEqual(len(results), 1)
            self.assertIn("Python", results[0]["text"])
            self.assertEqual(manager.context_entries(query="Japan travel", limit=1)[0]["text"], "I am planning a trip to Japan")
            with self.assertRaises(ValueError):
                manager.context_entries(query="hello", limit=11)

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
