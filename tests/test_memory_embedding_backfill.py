import tempfile
import unittest
from pathlib import Path

from core.agent_memory import AgentMemoryStore
from core.embedding_index import SQLiteEmbeddingIndex
from core.memory_embedding_backfill import MemoryEmbeddingBackfill


class BackfillEmbedder:
    is_local = True
    def __init__(self):
        self.calls = []
    def encode(self, text):
        self.calls.append(text)
        return [float("vehicle" in text.lower()), float("recipe" in text.lower()), 1.0]
    def encode_many(self, texts):
        return [self.encode(text) for text in texts]


class MemoryEmbeddingBackfillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = str(Path(self.temp.name) / "memory.sqlite3")
        allowed = {"agent_memory:write", "agent_memory:read"}
        self.store = AgentMemoryStore(
            self.db, authorize=lambda actor, action, _agent: actor == "owner" and action in allowed
        )
        for number in range(5):
            self.store.remember(
                actor_id="owner", agent_id="research",
                content=f"Research memory number {number} about vehicle technology",
                source=f"test:{number}",
            )
        self.embedder = BackfillEmbedder()
        self.index = SQLiteEmbeddingIndex(self.db, model_id="local-model@revision-1")
        self.backfill = MemoryEmbeddingBackfill(self.store, self.embedder, self.index)

    def test_backfill_resumes_from_durable_checkpoint(self):
        first = self.backfill.run(
            actor_id="owner", agent_id="research", page_size=2, max_pages=1
        )
        self.assertEqual(first["pages_processed"], 1)
        self.assertEqual(first["memories_indexed"], 2)
        self.assertFalse(first["completed"])
        first_call_count = len(self.embedder.calls)

        second = self.backfill.run(
            actor_id="owner", agent_id="research", page_size=2, max_pages=10
        )
        self.assertTrue(second["completed"])
        self.assertEqual(second["memories_indexed"], 3)
        self.assertEqual(len(self.embedder.calls), first_call_count + 3)
        self.assertEqual(self.index.stats(actor_id="owner")["vector_count"], 5)

        third = self.backfill.run(actor_id="owner", agent_id="research")
        self.assertTrue(third["completed"])
        self.assertEqual(third["pages_processed"], 0)

    def test_restart_reuses_existing_vectors_and_rebuilds_checkpoint(self):
        self.backfill.run(actor_id="owner", agent_id="research", page_size=2, max_pages=1)
        before = len(self.embedder.calls)
        result = self.backfill.run(
            actor_id="owner", agent_id="research", page_size=2,
            max_pages=10, restart=True,
        )
        self.assertTrue(result["completed"])
        self.assertEqual(len(self.embedder.calls), before)

    def test_visibility_scope_change_requires_explicit_restart(self):
        self.backfill.run(
            actor_id="owner", agent_id="research", page_size=2, max_pages=1,
            include_shared=False,
        )
        with self.assertRaisesRegex(ValueError, "visibility_scope_changed"):
            self.backfill.run(
                actor_id="owner", agent_id="research", page_size=2,
                include_shared=True,
            )
        restarted = self.backfill.run(
            actor_id="owner", agent_id="research", page_size=2,
            max_pages=10, include_shared=True, restart=True,
        )
        self.assertTrue(restarted["completed"])

    def test_backfill_requires_local_embedder_and_matching_database(self):
        with self.assertRaisesRegex(ValueError, "local_embedder"):
            MemoryEmbeddingBackfill(self.store, object(), self.index)
        other = SQLiteEmbeddingIndex(
            str(Path(self.temp.name) / "other.sqlite3"), model_id="local-model"
        )
        with self.assertRaisesRegex(ValueError, "share_database"):
            MemoryEmbeddingBackfill(self.store, self.embedder, other)

    def test_invalid_batch_bounds_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "page_size"):
            self.backfill.run(actor_id="owner", agent_id="research", page_size=101)
        with self.assertRaisesRegex(ValueError, "max_pages"):
            self.backfill.run(actor_id="owner", agent_id="research", max_pages=0)


if __name__ == "__main__":
    unittest.main()
