import tempfile
import unittest
from pathlib import Path

from core.agent_memory import AgentMemoryStore
from core.embedding_index import SQLiteEmbeddingIndex
from core.semantic_memory import SemanticMemoryRetriever


class FakeLocalEmbedder:
    is_local = True
    def __init__(self):
        self.calls = []
    def encode(self, text):
        self.calls.append(text)
        value = text.lower()
        return [float("car" in value or "vehicle" in value or "automobile" in value),
                float("food" in value or "recipe" in value), 1.0]


class PersistentEmbeddingIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = str(Path(self.tmp.name) / "memory.sqlite3")
        self.store = AgentMemoryStore(
            self.db,
            authorize=lambda actor, action, _agent: actor == "owner" and action in {
                "agent_memory:write", "agent_memory:share", "agent_memory:read",
                "agent_memory:delete", "agent_memory:prune"
            },
        )
        self.memories = [
            self.store.remember(actor_id="owner", agent_id="research",
                content="A vehicle is an automobile.", source="test:vehicle"),
            self.store.remember(actor_id="owner", agent_id="research",
                content="A recipe describes food preparation.", source="test:food"),
        ]
        self.embedder = FakeLocalEmbedder()
        self.index = SQLiteEmbeddingIndex(self.db, model_id="test-model@v1")
        self.retriever = SemanticMemoryRetriever(
            self.store, self.embedder, vector_index=self.index,
        )

    def test_first_search_builds_index_second_search_reuses_vectors(self):
        first = self.retriever.search(actor_id="owner", agent_id="research", query="automobile")
        first_calls = len(self.embedder.calls)
        self.assertEqual(first_calls, 3)  # query + two memory records
        self.assertTrue(all(item["embedding_cached"] for item in first))
        second = self.retriever.search(actor_id="owner", agent_id="research", query="vehicle")
        self.assertEqual(len(self.embedder.calls), first_calls + 1)  # query only
        self.assertTrue(all(item["embedding_cached"] for item in second))
        self.assertEqual(self.index.stats(actor_id="owner")["vector_count"], 2)

    def test_model_versions_are_isolated(self):
        self.retriever.search(actor_id="owner", agent_id="research", query="automobile")
        other = SQLiteEmbeddingIndex(self.db, model_id="test-model@v2")
        self.assertEqual(other.stats(actor_id="owner")["vector_count"], 0)

    def test_changed_memory_content_invalidates_cached_vector(self):
        memory = self.memories[0]
        self.index.put_many(actor_id="owner", memories=[memory], vectors=[[1.0, 0.0, 0.0]])
        changed = {**memory, "content": "An entirely different topic."}
        self.assertEqual(self.index.get_many(actor_id="owner", memories=[changed]), {})

    def test_vectors_are_scoped_by_actor(self):
        self.index.put_many(actor_id="owner", memories=[self.memories[0]], vectors=[[1.0, 0.0, 0.0]])
        self.assertEqual(self.index.get_many(actor_id="intruder", memories=[self.memories[0]]), {})

    def test_index_and_memory_store_must_share_database(self):
        other = SQLiteEmbeddingIndex(str(Path(self.tmp.name) / "other.sqlite3"), model_id="test")
        with self.assertRaisesRegex(ValueError, "share_memory_database"):
            SemanticMemoryRetriever(self.store, self.embedder, vector_index=other)

    def test_invalid_vector_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "finite"):
            self.index.put_many(actor_id="owner", memories=[self.memories[0]],
                                vectors=[[float("nan"), 1.0]])

    def test_delete_memory_removes_cached_vectors_atomically(self):
        memory = self.memories[0]
        self.index.put_many(actor_id="owner", memories=[memory], vectors=[[1.0, 0.0, 0.0]])
        self.assertTrue(self.store.delete(
            actor_id="owner", agent_id="research", memory_id=memory["memory_id"]
        ))
        self.assertEqual(
            self.index.get_many(actor_id="owner", memories=[memory]), {}
        )

    def test_prune_expired_removes_cached_vectors(self):
        memory = self.store.remember(
            actor_id="owner", agent_id="research", content="temporary memory",
            source="test:expiry",
        )
        self.index.put_many(actor_id="owner", memories=[memory], vectors=[[0.0, 1.0, 0.0]])
        with self.store._connect() as db:
            db.execute(
                "UPDATE agent_memories SET expires_at=? WHERE memory_id=?",
                ("2000-01-01T00:00:00+00:00", memory["memory_id"]),
            )
        self.assertEqual(self.store.prune_expired(actor_id="owner"), 1)
        self.assertEqual(
            self.index.get_many(actor_id="owner", memories=[memory]), {}
        )

    def test_reopen_preserves_index(self):
        self.retriever.search(actor_id="owner", agent_id="research", query="automobile")
        reopened = SQLiteEmbeddingIndex(self.db, model_id="test-model@v1")
        self.assertEqual(reopened.stats(actor_id="owner")["vector_count"], 2)


if __name__ == "__main__":
    unittest.main()
