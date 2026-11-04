import unittest

from core.memory_retrieval_evaluation import (
    audit_retrieval,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank,
)


class MemoryRetrievalEvaluationTests(unittest.TestCase):
    def test_recall_at_k_counts_distinct_relevant_memories(self):
        self.assertEqual(
            recall_at_k(["a", "a", "x", "b"], {"a", "b", "c"}, k=3),
            1 / 3,
        )

    def test_recall_is_zero_when_no_relevant_memories_are_known(self):
        self.assertEqual(recall_at_k(["a"], set()), 0.0)

    def test_reciprocal_rank_uses_first_relevant_result(self):
        self.assertEqual(reciprocal_rank(["x", "b", "a"], {"a", "b"}), 0.5)
        self.assertEqual(reciprocal_rank(["x"], {"a"}), 0.0)

    def test_ndcg_rewards_better_ordering_and_graded_relevance(self):
        relevance = {"high": 3, "medium": 2, "low": 1}
        ideal = ndcg_at_k(["high", "medium", "low"], relevance, k=3)
        reversed_order = ndcg_at_k(["low", "medium", "high"], relevance, k=3)
        self.assertEqual(ideal, 1.0)
        self.assertLess(reversed_order, ideal)

    def test_ndcg_is_zero_for_zero_relevance(self):
        self.assertEqual(ndcg_at_k(["a"], {"a": 0}), 0.0)

    def test_invalid_metric_inputs_are_rejected(self):
        for k in (0, -1, True, 1.5):
            with self.subTest(k=k), self.assertRaises(ValueError):
                recall_at_k([], {"a"}, k=k)
        with self.assertRaises(ValueError):
            ndcg_at_k([], {"a": float("nan")})
        with self.assertRaises(ValueError):
            ndcg_at_k([], {"a": -1})

    def test_audit_detects_permission_stale_and_duplicate_leakage(self):
        result = audit_retrieval(
            ["allowed-active", "private-other-owner", "deleted", "allowed-active"],
            authorized_ids={"allowed-active", "deleted"},
            active_ids={"allowed-active"},
        )
        self.assertEqual(result["unauthorized_count"], 1)
        self.assertEqual(result["stale_count"], 1)
        self.assertEqual(result["duplicate_count"], 1)
        self.assertFalse(result["permission_leakage_free"])
        self.assertFalse(result["stale_record_leakage_free"])

    def test_clean_retrieval_has_zero_isolation_leakage(self):
        result = audit_retrieval(
            ["a", "b"], authorized_ids={"a", "b"}, active_ids={"a", "b"},
        )
        self.assertTrue(result["permission_leakage_free"])
        self.assertTrue(result["stale_record_leakage_free"])
        self.assertEqual(result["duplicate_count"], 0)


if __name__ == "__main__":
    unittest.main()
