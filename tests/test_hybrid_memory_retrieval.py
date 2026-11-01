import tempfile
import unittest
from pathlib import Path

from memory.collective_vault import CollectiveMemoryVault
from memory.hybrid_retrieval import HybridMemoryRetriever, RetrievalError


class HybridMemoryRetrieverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.allowed = {"alice"}
        self.vault = CollectiveMemoryVault(
            str(Path(self.tmp.name) / "memory.sqlite3"),
            authorize=lambda actor, action, resource: actor in self.allowed and action == "memory:read"
            or actor in self.allowed and action in {"memory:write", "memory:update"},
        )
        for content in (
            "Kubernetes deployment rollback and release safety",
            "Python vector search semantic retrieval embeddings",
            "Quarterly finance planning and budget review",
        ):
            self.vault.remember(
                actor_id="alice", scope="project", scope_id="yuki",
                category="lesson", content=content,
                source_type="test", source_ref="tests/fixture",
            )

    def test_lexical_fallback_works_without_provider(self):
        retriever = HybridMemoryRetriever(self.vault)
        results = retriever.search(
            actor_id="alice", query="semantic retrieval embeddings",
            scopes=(("project", "yuki"),),
        )
        self.assertTrue(results)
        self.assertIn("semantic retrieval", results[0].record.content)
        self.assertIsNone(results[0].semantic_score)

    def test_semantic_similarity_finds_related_content(self):
        vectors = {
            "find meaning-based search": (1.0, 0.0),
            "Python vector search semantic retrieval embeddings": (0.98, 0.02),
            "Kubernetes deployment rollback and release safety": (0.0, 1.0),
            "Quarterly finance planning and budget review": (-1.0, 0.0),
        }
        retriever = HybridMemoryRetriever(
            self.vault, embed=lambda text: vectors[text], lexical_weight=0.0,
        )
        results = retriever.search(
            actor_id="alice", query="find meaning-based search",
            scopes=(("project", "yuki"),),
        )
        self.assertEqual(results[0].record.content, "Python vector search semantic retrieval embeddings")
        self.assertIsNotNone(results[0].semantic_score)

    def test_scope_acl_is_applied_before_embedding(self):
        called = []
        self.allowed.clear()
        retriever = HybridMemoryRetriever(self.vault, embed=lambda text: called.append(text) or (1.0, 0.0))
        results = retriever.search(
            actor_id="mallory", query="secret",
            scopes=(("project", "yuki"),),
        )
        self.assertEqual(results, ())
        self.assertEqual(called, [])

    def test_dimension_mismatch_fails_without_partial_results(self):
        count = {"n": 0}
        def embed(_):
            count["n"] += 1
            return (1.0, 0.0) if count["n"] == 1 else (1.0, 0.0, 0.0)
        retriever = HybridMemoryRetriever(self.vault, embed=embed)
        with self.assertRaises(RetrievalError):
            retriever.search(
                actor_id="alice", query="search",
                scopes=(("project", "yuki"),),
            )

    def test_invalid_nonfinite_embedding_rejected(self):
        retriever = HybridMemoryRetriever(self.vault, embed=lambda _: (float("nan"), 1.0))
        with self.assertRaises(RetrievalError):
            retriever.search(
                actor_id="alice", query="search",
                scopes=(("project", "yuki"),),
            )

    def test_provider_failure_does_not_fall_back_silently(self):
        retriever = HybridMemoryRetriever(
            self.vault, embed=lambda _: (_ for _ in ()).throw(RuntimeError("provider offline"))
        )
        with self.assertRaises(RetrievalError):
            retriever.search(
                actor_id="alice", query="semantic search",
                scopes=(("project", "yuki"),),
            )

    def test_weights_and_limits_are_validated(self):
        with self.assertRaises(RetrievalError):
            HybridMemoryRetriever(self.vault, lexical_weight=2)
        with self.assertRaises(RetrievalError):
            HybridMemoryRetriever(self.vault, candidate_limit=0)


if __name__ == "__main__":
    unittest.main()
