import tempfile
import unittest
from pathlib import Path

from memory.collective_vault import CollectiveMemoryVault
from memory.intelligence import MemoryIntelligenceEngine, MemoryIntelligenceError


class MemoryIntelligenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.allowed = True
        self.vault = CollectiveMemoryVault(
            str(Path(self.tmp.name) / "memory.sqlite3"),
            authorize=lambda actor, action, resource: self.allowed and action in {
                "memory:write", "memory:read"
            },
        )

    def add(self, content, scope_id="yuki"):
        return self.vault.remember(
            actor_id="alice", scope="project", scope_id=scope_id,
            category="fact", content=content, source_type="test",
            source_ref="tests/memory-intelligence",
        )

    def test_exact_duplicates_are_reported_without_mutation(self):
        first = self.add("The release pipeline uses signed artifacts.")
        second = self.add("The release pipeline uses signed artifacts.")
        before = self.vault.get(actor_id="alice", memory_id=first.memory_id)
        findings = MemoryIntelligenceEngine(self.vault).analyze(
            actor_id="alice", scopes=(("project", "yuki"),)
        )
        self.assertEqual(findings[0].kind, "exact_duplicate")
        self.assertTrue(findings[0].requires_human_review)
        self.assertEqual(self.vault.get(actor_id="alice", memory_id=first.memory_id), before)
        self.assertIsNotNone(self.vault.get(actor_id="alice", memory_id=second.memory_id))

    def test_opposing_terms_flag_possible_contradiction(self):
        self.add("The feature flag is enabled for production.")
        self.add("The feature flag is disabled for production.")
        findings = MemoryIntelligenceEngine(self.vault).analyze(
            actor_id="alice", scopes=(("project", "yuki"),)
        )
        self.assertTrue(any(item.kind == "possible_contradiction" for item in findings))

    def test_semantic_provider_can_find_duplicate_candidates(self):
        self.add("Yuki retrieves relevant project memories.")
        self.add("Yuki searches useful project knowledge.")
        vectors = {
            "Yuki retrieves relevant project memories.": (1.0, 0.0),
            "Yuki searches useful project knowledge.": (0.99, 0.01),
        }
        findings = MemoryIntelligenceEngine(
            self.vault, embed=lambda text: vectors[text], duplicate_threshold=0.98
        ).analyze(actor_id="alice", scopes=(("project", "yuki"),))
        self.assertTrue(any(item.kind == "possible_duplicate" for item in findings))

    def test_acl_denied_means_no_provider_invocation(self):
        self.add("Confidential project memory.")
        self.allowed = False
        calls = []
        findings = MemoryIntelligenceEngine(
            self.vault, embed=lambda text: calls.append(text) or (1.0, 0.0)
        ).analyze(actor_id="mallory", scopes=(("project", "yuki"),))
        self.assertEqual(findings, ())
        self.assertEqual(calls, [])

    def test_provider_failure_aborts_analysis(self):
        self.add("One memory.")
        with self.assertRaises(MemoryIntelligenceError):
            MemoryIntelligenceEngine(
                self.vault, embed=lambda _: (_ for _ in ()).throw(RuntimeError("offline"))
            ).analyze(actor_id="alice", scopes=(("project", "yuki"),))

    def test_invalid_vector_is_rejected(self):
        self.add("One memory.")
        with self.assertRaises(MemoryIntelligenceError):
            MemoryIntelligenceEngine(
                self.vault, embed=lambda _: (float("nan"),)
            ).analyze(actor_id="alice", scopes=(("project", "yuki"),))

    def test_scope_boundaries_are_respected(self):
        self.add("A shared statement.", "yuki")
        self.add("A shared statement.", "other")
        findings = MemoryIntelligenceEngine(self.vault).analyze(
            actor_id="alice", scopes=(("project", "yuki"),)
        )
        self.assertEqual(findings, ())

    def test_invalid_limits_are_rejected(self):
        with self.assertRaises(MemoryIntelligenceError):
            MemoryIntelligenceEngine(self.vault, candidate_limit=0)
        with self.assertRaises(MemoryIntelligenceError):
            MemoryIntelligenceEngine(self.vault).analyze(
                actor_id="alice", scopes=(("project", "yuki"),), limit=0
            )

    def test_zero_vector_is_rejected_even_for_single_record(self):
        self.add("One memory.")
        with self.assertRaises(MemoryIntelligenceError):
            MemoryIntelligenceEngine(
                self.vault, embed=lambda _: (0.0, 0.0)
            ).analyze(actor_id="alice", scopes=(("project", "yuki"),))

    def test_boolean_embedding_values_are_rejected(self):
        self.add("One memory.")
        with self.assertRaises(MemoryIntelligenceError):
            MemoryIntelligenceEngine(
                self.vault, embed=lambda _: (True, 0.5)
            ).analyze(actor_id="alice", scopes=(("project", "yuki"),))


if __name__ == "__main__":
    unittest.main()
