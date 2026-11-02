import tempfile
import unittest
from pathlib import Path

from core.memory import MemoryStore
from core.memory_retrieval import MemoryRetriever


class MemoryRetrieverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = MemoryStore(Path(self.temp.name) / "memory.sqlite3")
        self.retriever = MemoryRetriever(self.store)

    def add(self, content, actor="alice", scope="personal:alice", category="fact"):
        return self.store.put(actor_id=actor, scope_id=scope,
            category=category, content=content)

    def test_ranks_relevant_memories_and_reports_matched_terms(self):
        relevant = self.add("Yuki uses SQLite for persistent memory storage")
        self.add("The desktop interface uses a compact navigation layout")
        results = self.retriever.retrieve(actor_id="alice",
            scope_id="personal:alice", query="SQLite memory storage")
        self.assertEqual(results[0].memory.memory_id, relevant.memory_id)
        self.assertEqual(set(results[0].matched_terms), {"sqlite", "memory", "storage"})
        self.assertGreater(results[0].score, 0)

    def test_never_returns_other_actor_or_scope(self):
        own = self.add("private project database architecture")
        self.add("private project database architecture", actor="bob", scope="personal:bob")
        self.add("private project database architecture", scope="project:other")
        results = self.retriever.retrieve(actor_id="alice",
            scope_id="personal:alice", query="project database")
        self.assertEqual([x.memory.memory_id for x in results], [own.memory_id])

    def test_category_filter_is_respected(self):
        self.add("local model inference", category="preference")
        self.add("local model deployment", category="procedure")
        results = self.retriever.retrieve(actor_id="alice",
            scope_id="personal:alice", query="local model", category="preference")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].memory.category, "preference")

    def test_no_match_returns_empty(self):
        self.add("SQLite memory storage")
        self.assertEqual(self.retriever.retrieve(actor_id="alice",
            scope_id="personal:alice", query="voice wakeword"), [])

    def test_validation_and_stop_words(self):
        with self.assertRaises(ValueError):
            self.retriever.retrieve(actor_id="alice", scope_id="personal:alice", query=" ")
        self.assertEqual(self.retriever.retrieve(actor_id="alice",
            scope_id="personal:alice", query="the and is"), [])


if __name__ == "__main__":
    unittest.main()
