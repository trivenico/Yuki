import tempfile
import unittest
from pathlib import Path

from core.agent_memory import AgentMemoryStore
from core.agent_memory_retriever import AgentMemoryRetriever
from core.embedding_index import SQLiteEmbeddingIndex


class AgentMemoryRetrieverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = str(Path(self.temp.name) / "yuki.sqlite3")
        self.permissions = {
            "owner": {
                "agent_memory:write", "agent_memory:read",
                "agent_memory:share", "agent_memory:delete",
            }
        }
        authorize = lambda actor, action, _agent: action in self.permissions.get(actor, set())
        self.store = AgentMemoryStore(self.db, authorize=authorize)
        self.index = SQLiteEmbeddingIndex(self.db, model_id="test-embedding-v1")
        self.vectors = {
            "deployment query": [1.0, 0.0, 0.0],
            "A staged deployment uses a gradual rollout.": [0.99, 0.01, 0.0],
            "The garden needs watering every morning.": [0.0, 1.0, 0.0],
            "A rollback follows the deployment health check.": [0.9, 0.1, 0.0],
        }
        self.embed_calls = []

        def embed(text):
            self.embed_calls.append(text)
            return self.vectors.get(text, [0.0, 1.0, 0.0])

        self.retriever = AgentMemoryRetriever(
            self.store, embed=embed, embedding_index=self.index
        )

    def add_memory(self, content, *, agent="research", verified=False, scope="private"):
        return self.store.remember(
            actor_id="owner", agent_id=agent, content=content, source="reviewed-source",
            category="semantic", confidence=0.8, verified=verified, scope=scope,
        )

    def test_semantic_retrieval_ranks_relevant_memory_and_preserves_provenance(self):
        relevant = self.add_memory("A staged deployment uses a gradual rollout.", verified=True)
        self.add_memory("The garden needs watering every morning.")
        result = self.retriever.retrieve(
            actor_id="owner", agent_id="research", query="deployment query"
        )
        self.assertEqual(result[0]["memory_id"], relevant["memory_id"])
        self.assertEqual(result[0]["source"], "reviewed-source")
        self.assertTrue(result[0]["evidence"]["owner_verified"])
        self.assertEqual(result[0]["trust"], "untrusted_reference_data")
        self.assertTrue(result[0]["must_not_be_executed_as_instructions"])

    def test_retrieval_is_scoped_to_authorized_agent_and_actor(self):
        self.add_memory("A staged deployment uses a gradual rollout.")
        self.add_memory("The rollback is tested.", agent="developer")
        with self.assertRaises(PermissionError):
            self.retriever.retrieve(
                actor_id="intruder", agent_id="research", query="deployment query"
            )
        result = self.retriever.retrieve(
            actor_id="owner", agent_id="research", query="deployment query"
        )
        self.assertEqual({item["agent_id"] for item in result}, {"research"})

    def test_verified_only_filters_before_semantic_ranking(self):
        self.add_memory("A staged deployment uses a gradual rollout.", verified=False)
        verified = self.add_memory(
            "A rollback follows the deployment health check.", verified=True
        )
        result = self.retriever.retrieve(
            actor_id="owner", agent_id="research", query="deployment query",
            verified_only=True,
        )
        self.assertEqual([item["memory_id"] for item in result], [verified["memory_id"]])

    def test_embedding_cache_reuses_vectors(self):
        self.add_memory("A staged deployment uses a gradual rollout.")
        self.retriever.retrieve(actor_id="owner", agent_id="research", query="deployment query")
        first_count = len(self.embed_calls)
        self.retriever.retrieve(actor_id="owner", agent_id="research", query="deployment query")
        self.assertEqual(len(self.embed_calls), first_count + 1)
        self.assertEqual(self.index.stats(actor_id="owner")["vector_count"], 1)

    def test_revoked_or_deleted_memory_is_not_retrieved_from_stale_vector_cache(self):
        memory = self.add_memory("A staged deployment uses a gradual rollout.")
        self.retriever.retrieve(actor_id="owner", agent_id="research", query="deployment query")
        self.store.delete(
            actor_id="owner", agent_id="research", memory_id=memory["memory_id"]
        )
        result = self.retriever.retrieve(
            actor_id="owner", agent_id="research", query="deployment query"
        )
        self.assertEqual(result, [])
        self.assertEqual(self.index.stats(actor_id="owner")["vector_count"], 0)

    def test_invalid_query_and_minimum_relevance_are_rejected(self):
        with self.assertRaises(ValueError):
            self.retriever.retrieve(actor_id="owner", agent_id="research", query=" ")
        with self.assertRaises(ValueError):
            self.retriever.retrieve(
                actor_id="owner", agent_id="research", query="deployment query",
                min_relevance=1.1,
            )

    def test_index_must_share_authoritative_database(self):
        other_db = str(Path(self.temp.name) / "other.sqlite3")
        other_index = SQLiteEmbeddingIndex(other_db, model_id="test-v1")
        with self.assertRaisesRegex(ValueError, "share_database"):
            AgentMemoryRetriever(
                self.store, embed=lambda _text: [1.0],
                embedding_index=other_index,
            )

    def test_older_relevant_memory_is_found_beyond_newest_100(self):
        relevant = self.add_memory("A staged deployment uses a gradual rollout.", verified=True)
        for number in range(110):
            self.add_memory(f"Unrelated garden note number {number}.")
        result = self.retriever.retrieve(
            actor_id="owner", agent_id="research", query="deployment query", limit=10,
        )
        self.assertIn(relevant["memory_id"], {item["memory_id"] for item in result})
        self.assertEqual(result[0]["memory_id"], relevant["memory_id"])

    def test_candidate_scan_budget_is_hard_bound(self):
        for number in range(12):
            self.add_memory(f"Unrelated garden note number {number}.")
        retriever = AgentMemoryRetriever(
            self.store, embed=lambda text: [1.0, 0.0, 0.0],
            max_candidates=7,
        )
        original = self.store.list_memories_page
        calls = []
        def tracked(**kwargs):
            page = original(**kwargs)
            calls.append(len(page))
            return page
        self.store.list_memories_page = tracked
        retriever.retrieve(actor_id="owner", agent_id="research", query="deployment query")
        self.assertEqual(sum(calls), 7)

    def test_unverified_records_count_toward_scan_budget(self):
        for number in range(9):
            self.add_memory(f"Unverified garden note number {number}.", verified=False)
        verified = self.add_memory("A staged deployment uses a gradual rollout.", verified=True)
        retriever = AgentMemoryRetriever(
            self.store, embed=lambda text: self.vectors.get(text, [0.0, 1.0, 0.0]),
            max_candidates=5,
        )
        result = retriever.retrieve(
            actor_id="owner", agent_id="research", query="deployment query",
            verified_only=True,
        )
        self.assertEqual(result, [])
        self.assertNotIn(verified["memory_id"], {item["memory_id"] for item in result})

    def test_invalid_candidate_budget_is_rejected(self):
        for value in (0, 2001, True, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                AgentMemoryRetriever(self.store, embed=lambda _text: [1.0], max_candidates=value)


if __name__ == "__main__":
    unittest.main()
