import tempfile
import unittest
from pathlib import Path

from core.memory import MemoryStore
from core.memory_intelligence import MemoryIntelligence


class MemoryIntelligenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = MemoryStore(Path(self.temp.name) / "memory.sqlite3")
        self.engine = MemoryIntelligence(self.store)

    def test_extracts_explicit_preferences_and_decisions(self):
        found = self.engine.extract(
            "I prefer local models. We have decided to use SQLite for memory."
        )
        self.assertEqual([x.category for x in found], ["preference", "decision"])
        self.assertTrue(all(0 <= x.confidence <= 1 for x in found))
        self.assertTrue(all(x.evidence for x in found))

    def test_remember_instruction_is_high_confidence_candidate_only(self):
        found = self.engine.extract("Please remember that deployments require approval.")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].category, "fact")
        self.assertGreaterEqual(found[0].confidence, 0.9)

    def test_never_persists_candidates_automatically(self):
        self.engine.extract("I prefer concise answers.")
        self.assertEqual(self.store.list(actor_id="alice",
            scope_id="personal:alice"), [])

    def test_marks_duplicate_without_overwriting(self):
        saved = self.store.put(actor_id="alice", scope_id="personal:alice",
            category="preference", content="local models")
        found = self.engine.extract("I prefer local models.",
            actor_id="alice", scope_id="personal:alice")
        self.assertEqual(found[0].duplicate_of, saved.memory_id)
        self.assertEqual(self.store.get(actor_id="alice",
            scope_id="personal:alice", memory_id=saved.memory_id).content, "local models")

    def test_flags_possible_conflict_for_review(self):
        self.store.put(actor_id="alice", scope_id="personal:alice",
            category="preference", content="Use local models")
        found = self.engine.extract("I prefer not to use local models.",
            actor_id="alice", scope_id="personal:alice")
        self.assertTrue(found[0].possible_conflict_with)

    def test_scope_arguments_must_be_paired(self):
        with self.assertRaises(ValueError):
            self.engine.extract("I prefer local models", actor_id="alice")

    def test_rejects_invalid_or_oversized_input(self):
        for value in ("", "  ", "x" * 12001):
            with self.assertRaises(ValueError):
                self.engine.extract(value)


if __name__ == "__main__":
    unittest.main()
