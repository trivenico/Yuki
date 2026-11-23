import tempfile
import unittest
from pathlib import Path

from companion.memory import MemoryStore
from companion.memory_consent import CompanionMemory


class CompanionMemoryContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = MemoryStore(Path(self.temp.name) / "memory.json")
        self.memory = CompanionMemory(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def test_context_requires_explicit_session_consent(self):
        self.store.add("Use bounded retries for worker recovery.", category="lesson")
        self.assertEqual(self.memory.context_entries(query="worker recovery"), [])
        self.memory.enable_context()
        results = self.memory.context_entries(query="worker recovery")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["use_count"], 1)

    def test_context_excludes_archived_entries_and_tracks_only_retrieved_use(self):
        active = self.store.add("Use bounded retries for worker recovery.", category="lesson")
        archived = self.store.add("Old worker recovery approach.", category="lesson")
        self.store.update_lifecycle(
            archived["id"], expected_version=1, status="archived",
            reason="Superseded by the current retry policy.",
        )
        self.memory.enable_context()
        results = self.memory.context_entries(query="worker recovery")
        self.assertEqual([item["id"] for item in results], [active["id"]])
        stored = {item["id"]: item for item in self.store.list(limit=2)}
        self.assertEqual(stored[active["id"]]["use_count"], 1)
        self.assertEqual(stored[archived["id"]]["use_count"], 0)

    def test_empty_query_does_not_return_all_memories(self):
        self.store.add("Saved note that should not be injected automatically.")
        self.memory.enable_context()
        self.assertEqual(self.memory.context_entries(query="   "), [])


if __name__ == "__main__":
    unittest.main()
