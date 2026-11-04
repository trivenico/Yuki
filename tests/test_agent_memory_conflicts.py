import tempfile
import unittest
from pathlib import Path

from core.agent_memory import AgentMemoryStore
from core.agent_memory_conflicts import MemoryConflictDetector


class MemoryConflictDetectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = str(Path(self.temp.name) / "yuki.sqlite3")
        permissions = {
            "owner": {"agent_memory:write", "agent_memory:read", "agent_memory:share"}
        }
        self.store = AgentMemoryStore(
            self.db,
            authorize=lambda actor, action, _agent: action in permissions.get(actor, set()),
        )
        self.detector = MemoryConflictDetector(self.store)

    def add(self, content, *, agent="research", scope="private", source="source"):
        return self.store.remember(
            actor_id="owner", agent_id=agent, content=content, source=source,
            category="semantic", confidence=0.8, verified=False, scope=scope,
        )

    def test_explicit_opposite_state_terms_are_flagged_without_mutating_memory(self):
        first = self.add("The service is enabled.")
        second = self.add("The service is disabled.")
        result = self.detector.detect(actor_id="owner", agent_id="research")
        self.assertEqual(len(result), 1)
        self.assertEqual(set(result[0]["memory_ids"]), {
            first["memory_id"], second["memory_id"]
        })
        self.assertEqual(result[0]["status"], "needs_owner_review")
        self.assertTrue(result[0]["signals"]["opposite_state_terms"])
        self.assertFalse(result[0]["memory_mutated"])
        self.assertEqual(len(self.store.list_memories(
            actor_id="owner", agent_id="research")), 2)

    def test_opposing_negation_is_flagged_for_same_subject(self):
        self.add("The deployment is available.")
        self.add("The deployment is not available.")
        result = self.detector.detect(actor_id="owner", agent_id="research")
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]["signals"]["opposing_negation"])

    def test_unrelated_claims_are_not_flagged(self):
        self.add("The service is enabled.")
        self.add("The database is disabled.")
        self.assertEqual(
            self.detector.detect(actor_id="owner", agent_id="research"), []
        )

    def test_contextual_differences_are_not_silently_merged(self):
        self.add("The service is enabled in staging.")
        self.add("The service is disabled in production.")
        self.assertEqual(
            self.detector.detect(actor_id="owner", agent_id="research"), []
        )

    def test_shared_memory_conflicts_are_detected_with_both_provenances(self):
        self.add("The feature is enabled.", scope="shared", source="source-a")
        self.add(
            "The feature is disabled.", agent="developer",
            scope="shared", source="source-b",
        )
        result = self.detector.detect(
            actor_id="owner", agent_id="research", include_shared=True
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(
            {claim["source"] for claim in result[0]["claims"]},
            {"source-a", "source-b"},
        )
        self.assertEqual(
            {claim["agent_id"] for claim in result[0]["claims"]},
            {"research", "developer"},
        )

    def test_shared_conflicts_can_be_excluded(self):
        self.add("The feature is enabled.", scope="shared")
        self.add("The feature is disabled.", scope="shared", agent="developer")
        self.assertEqual(self.detector.detect(
            actor_id="owner", agent_id="research", include_shared=False
        ), [])

    def test_read_permission_is_enforced_by_memory_store(self):
        with self.assertRaises(PermissionError):
            self.detector.detect(actor_id="intruder", agent_id="research")

    def test_invalid_limits_and_thresholds_are_rejected(self):
        for kwargs in ({"limit": 1}, {"min_overlap": 0.4}, {"min_overlap": True}):
            with self.assertRaises(ValueError):
                self.detector.detect(
                    actor_id="owner", agent_id="research", **kwargs
                )


    def test_different_numeric_values_with_same_unit_are_flagged(self):
        first = self.add("The request timeout is 30 seconds.")
        second = self.add("The request timeout is 60 seconds.")
        result = self.detector.detect(actor_id="owner", agent_id="research")
        self.assertEqual(len(result), 1)
        self.assertEqual(set(result[0]["memory_ids"]), {
            first["memory_id"], second["memory_id"]
        })
        self.assertTrue(result[0]["signals"]["numeric_measurement_conflict"])
        self.assertFalse(result[0]["memory_mutated"])

    def test_same_numeric_measurement_is_not_flagged(self):
        self.add("The request timeout is 30 seconds.")
        self.add("The request timeout is 30 seconds.")
        self.assertEqual(
            self.detector.detect(actor_id="owner", agent_id="research"), []
        )

    def test_different_units_are_not_assumed_equivalent_or_conflicting(self):
        self.add("The request timeout is 30 seconds.")
        self.add("The request timeout is 30 minutes.")
        result = self.detector.detect(actor_id="owner", agent_id="research")
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
