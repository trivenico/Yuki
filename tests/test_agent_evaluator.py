import unittest

from evaluation.agent_evaluator import AgentEvaluator


class AgentEvaluatorTests(unittest.TestCase):
    def setUp(self):
        self.evaluator = AgentEvaluator()
        self.signals = {
            "correctness": 1.0,
            "evidence_quality": 1.0,
            "instruction_compliance": True,
            "completeness": 0.8,
            "efficiency": 0.6,
        }

    def evaluate(self, **overrides):
        args = dict(task_id="task-123", agent_id="research", outcome="succeeded",
                    signals=self.signals, evidence_refs=["trace:123", "artifact:123"])
        args.update(overrides)
        return self.evaluator.evaluate(**args)

    def test_weighted_score_and_confidence_are_deterministic(self):
        report = self.evaluate()
        self.assertEqual(report.score, 0.95)
        self.assertEqual(report.confidence, "high")
        self.assertFalse(report.requires_human_review)

    def test_failed_or_cancelled_task_cannot_score_as_success(self):
        for outcome in ("failed", "cancelled"):
            report = self.evaluate(outcome=outcome)
            self.assertEqual(report.score, 0.0)
            self.assertTrue(report.requires_human_review)
            self.assertFalse(report.eligible_for_memory_review)

    def test_missing_evidence_caps_score_and_requires_review(self):
        report = self.evaluate(evidence_refs=[])
        self.assertEqual(report.score, 0.49)
        self.assertEqual(report.confidence, "low")
        self.assertTrue(report.requires_human_review)

    def test_high_score_only_qualifies_for_review_not_memory_write(self):
        report = self.evaluate()
        self.assertTrue(report.eligible_for_memory_review)
        self.assertFalse(report.automatic_memory_write)
        self.assertFalse(report.routing_changed)

    def test_signal_schema_and_ranges_are_enforced(self):
        with self.assertRaises(ValueError):
            self.evaluate(signals={"correctness": 1.0})
        invalid = dict(self.signals, correctness=1.5)
        with self.assertRaises(ValueError):
            self.evaluate(signals=invalid)

    def test_evidence_refs_are_validated(self):
        with self.assertRaises(ValueError):
            self.evaluate(evidence_refs=["same", "same"])
        with self.assertRaises(ValueError):
            self.evaluate(evidence_refs=[" "])

    def test_url_safe_task_ids_may_start_with_underscore_or_hyphen(self):
        for task_id in ("_task-123", "-task-123"):
            with self.subTest(task_id=task_id):
                report = self.evaluate(task_id=task_id)
                self.assertEqual(report.task_id, task_id)
                self.assertEqual(report.evaluation_id, "eval-" + task_id)

    def test_rubric_weights_sum_to_one(self):
        self.assertAlmostEqual(sum(self.evaluator.rubric()["weights"].values()), 1.0)


if __name__ == "__main__":
    unittest.main()
