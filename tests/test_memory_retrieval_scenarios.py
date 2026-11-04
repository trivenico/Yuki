import unittest

from core.memory_retrieval_evaluation import (
    audit_retrieval,
    mean_reciprocal_rank,
    ndcg_at_k,
    recall_at_k,
)
from tests.memory_evaluation.scenarios import SCENARIOS


class MemoryRetrievalScenarioTests(unittest.TestCase):
    def test_scenario_ids_are_unique_and_bounded(self):
        names = [scenario.name for scenario in SCENARIOS]
        self.assertEqual(len(names), len(set(names)))
        for scenario in SCENARIOS:
            self.assertTrue(scenario.query)
            self.assertLessEqual(len(scenario.ranked_ids), 100)
            self.assertTrue(set(scenario.relevance_by_id).issubset(
                scenario.authorized_ids
            ))

    def test_scenarios_produce_deterministic_ranking_metrics(self):
        scores = []
        for scenario in SCENARIOS:
            scores.append((
                scenario.ranked_ids,
                scenario.relevant_ids,
            ))
            self.assertGreaterEqual(
                recall_at_k(scenario.ranked_ids, scenario.relevant_ids, k=5), 0.0
            )
            self.assertGreaterEqual(
                ndcg_at_k(scenario.ranked_ids, scenario.relevance_by_id, k=5), 0.0
            )
        self.assertEqual(mean_reciprocal_rank(scores), 1.0)

    def test_all_scenarios_have_zero_permission_and_stale_leakage(self):
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario.name):
                audit = audit_retrieval(
                    scenario.ranked_ids,
                    authorized_ids=scenario.authorized_ids,
                    active_ids=scenario.active_ids,
                )
                self.assertEqual(audit["unauthorized_count"], 0)
                self.assertEqual(audit["stale_count"], 0)

    def test_audit_fixture_detects_injected_isolation_violations(self):
        # Negative controls prove that the audit itself detects both leak types.
        audit = audit_retrieval(
            ("owner-private", "other-owner-private", "deleted-project"),
            authorized_ids={"owner-private", "deleted-project"},
            active_ids={"owner-private"},
        )
        self.assertEqual(audit["unauthorized_count"], 1)
        self.assertEqual(audit["stale_count"], 2)


if __name__ == "__main__":
    unittest.main()
