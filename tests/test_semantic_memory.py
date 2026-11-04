import tempfile
import unittest
from pathlib import Path

from core.agent_memory import AgentMemoryStore
from core.semantic_memory import SemanticMemoryRetriever
from automation.agent_memory_integration import build_memory_aware_handlers


class FakeLocalEmbedder:
    is_local = True

    def encode(self, text):
        # Deterministic stand-in for a local model in unit tests. Production
        # applications inject a real local embedding model adapter.
        value = text.lower()
        return [
            float(any(token in value for token in ("car", "vehicle", "automobile"))),
            float(any(token in value for token in ("food", "recipe", "cooking"))),
            float(any(token in value for token in ("python", "code", "software"))),
            1.0,
        ]


class SemanticMemoryRetrieverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = AgentMemoryStore(
            str(Path(self.tmp.name) / "memory.sqlite3"),
            authorize=lambda actor, action, _agent: (
                actor == "owner" and action in {"agent_memory:write", "agent_memory:read"}
            ),
        )
        self.store.remember(
            actor_id="owner", agent_id="research",
            content="A vehicle is another word for an automobile.",
            source="reviewed:vehicle-notes", verified=True,
        )
        self.store.remember(
            actor_id="owner", agent_id="research",
            content="A recipe explains food preparation and cooking.",
            source="reviewed:food-notes", verified=True,
        )
        self.store.remember(
            actor_id="owner", agent_id="coding",
            content="Python software code should be tested.",
            source="reviewed:code-notes", verified=True,
        )
        self.retriever = SemanticMemoryRetriever(self.store, FakeLocalEmbedder())

    def test_hybrid_retrieval_finds_semantic_match_and_preserves_scope(self):
        results = self.retriever.search(
            actor_id="owner", agent_id="research", query="automobile", limit=5,
        )
        self.assertEqual(len(results), 2)
        self.assertIn("vehicle", results[0]["content"])
        self.assertEqual(results[0]["retrieval"], "local_hybrid")
        self.assertGreaterEqual(results[0]["semantic_score"], results[1]["semantic_score"])

    def test_retrieval_is_actor_authorized(self):
        with self.assertRaises(PermissionError):
            self.retriever.search(
                actor_id="intruder", agent_id="research", query="vehicle",
            )

    def test_shared_memories_follow_store_scope_rules(self):
        results = self.retriever.search(
            actor_id="owner", agent_id="research", query="python",
            include_shared=False,
        )
        self.assertTrue(all(item["agent_id"] == "research" for item in results))

    def test_cloud_embedder_is_rejected(self):
        class CloudEmbedder:
            is_local = False
            def encode(self, _text):
                return [1.0, 0.0]

        with self.assertRaisesRegex(ValueError, "explicit_local_embedder"):
            SemanticMemoryRetriever(self.store, CloudEmbedder())

    def test_invalid_embedding_values_and_dimensions_are_rejected(self):
        class InvalidEmbedder:
            is_local = True
            def encode(self, _text):
                return [float("nan"), 1.0]

        retriever = SemanticMemoryRetriever(self.store, InvalidEmbedder())
        with self.assertRaisesRegex(ValueError, "finite"):
            retriever.search(actor_id="owner", agent_id="research", query="vehicle")

        class MismatchedEmbedder:
            is_local = True
            def encode(self, text):
                return [1.0, 0.0] if text == "vehicle" else [1.0, 0.0, 0.0]

        retriever = SemanticMemoryRetriever(self.store, MismatchedEmbedder())
        with self.assertRaisesRegex(ValueError, "dimensions_must_match"):
            retriever.search(actor_id="owner", agent_id="research", query="vehicle")

    def test_semantic_retrieval_can_be_enabled_for_specialist_handlers(self):
        handler = build_memory_aware_handlers(
            {"research": lambda prompt: prompt},
            self.store,
            actor_id="owner",
            retriever=self.retriever,
        )["research"]
        prompt = handler("automobile")
        self.assertIn("A vehicle", prompt)
        self.assertIn("untrusted reference data", prompt)
        self.assertIn("CURRENT_USER_TASK: automobile", prompt)

    def test_retriever_must_share_the_authorized_memory_store(self):
        other_store = AgentMemoryStore(
            str(Path(self.tmp.name) / "other-memory.sqlite3"),
            authorize=lambda _actor, _action, _agent: True,
        )
        other_retriever = SemanticMemoryRetriever(other_store, FakeLocalEmbedder())
        with self.assertRaisesRegex(ValueError, "same_authorized_memory_store"):
            build_memory_aware_handlers(
                {"research": lambda prompt: prompt},
                self.store,
                actor_id="owner",
                retriever=other_retriever,
            )

    def test_empty_scope_returns_no_results(self):
        results = self.retriever.search(
            actor_id="owner", agent_id="unknown-agent", query="vehicle",
        )
        self.assertEqual(results, [])

    def test_input_limits_are_enforced(self):
        with self.assertRaises(ValueError):
            self.retriever.search(actor_id="owner", agent_id="research", query=" ")
        with self.assertRaises(ValueError):
            self.retriever.search(actor_id="owner", agent_id="research", query="vehicle", limit=21)


if __name__ == "__main__":
    unittest.main()
