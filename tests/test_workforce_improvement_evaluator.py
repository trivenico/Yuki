import unittest

from core.workforce_improvement_evaluator import evaluate_improvement_snapshots


class WorkforceImprovementEvaluatorTests(unittest.TestCase):
    def test_reports_directional_metrics_without_causal_claims(self):
        baseline = {
            "tasks": {"succeeded": 8, "failed": 2, "queued": 4},
            "performance": {"independently_verified_successes": 5},
        }
        current = {
            "tasks": {"succeeded": 9, "failed": 1, "queued": 2},
            "performance": {"independently_verified_successes": 7},
        }
        result = evaluate_improvement_snapshots(baseline, current)
        self.assertEqual(result["status"], "descriptive_comparison")
        self.assertEqual(result["metrics"]["success_rate"]["baseline"], 0.8)
        self.assertEqual(result["metrics"]["success_rate"]["current"], 0.9)
        self.assertEqual(result["metrics"]["failed_tasks"]["status"], "favorable_direction")
        self.assertEqual(result["metrics"]["queued_tasks"]["status"], "favorable_direction")
        self.assertFalse(result["automatic_action_taken"])
        self.assertTrue(result["human_review_required"])
        self.assertIn("do not establish causation", result["interpretation"])

    def test_small_samples_are_explicitly_insufficient(self):
        snapshot = {
            "tasks": {"succeeded": 2, "failed": 1, "queued": 0},
            "performance": {"independently_verified_successes": 1},
        }
        result = evaluate_improvement_snapshots(snapshot, snapshot)
        self.assertEqual(result["status"], "insufficient_observations")
        self.assertEqual(result["metrics"]["success_rate"]["status"], "insufficient_observations")

    def test_missing_or_invalid_metrics_are_not_coerced(self):
        result = evaluate_improvement_snapshots(
            {"tasks": {"succeeded": True, "failed": 0}, "performance": {}},
            {"tasks": {"succeeded": 5, "failed": 0, "queued": 0}, "performance": {"independently_verified_successes": 3}},
        )
        self.assertEqual(result["status"], "insufficient_observations")
        self.assertFalse(result["metrics"]["success_rate"]["available"])
        self.assertEqual(result["metrics"]["queued_tasks"]["status"], "unavailable")

    def test_flags_inconsistent_verified_success_counts(self):
        baseline = {
            "tasks": {"succeeded": 4, "failed": 1, "queued": 0},
            "performance": {"independently_verified_successes": 5},
        }
        current = {
            "tasks": {"succeeded": 5, "failed": 1, "queued": 0},
            "performance": {"independently_verified_successes": 3},
        }
        result = evaluate_improvement_snapshots(
            baseline, current, minimum_completed_observations=1,
        )
        self.assertEqual(result["data_quality"]["status"], "warnings")
        self.assertIn(
            "verified_successes_exceed_succeeded_tasks",
            [item["code"] for item in result["data_quality"]["issues"]],
        )
        self.assertFalse(result["metrics"]["independently_verified_successes"]["available"])

    def test_flags_fractional_operational_counts(self):
        baseline = {
            "tasks": {"succeeded": 4.5, "failed": 1, "queued": 0},
            "performance": {"independently_verified_successes": 2},
        }
        current = {
            "tasks": {"succeeded": 5, "failed": 1, "queued": 0},
            "performance": {"independently_verified_successes": 3},
        }
        result = evaluate_improvement_snapshots(
            baseline, current, minimum_completed_observations=1,
        )
        self.assertIn(
            "non_integer_task_count",
            [item["code"] for item in result["data_quality"]["issues"]],
        )
        self.assertEqual(result["status"], "insufficient_observations")
        self.assertFalse(result["metrics"]["success_rate"]["available"])
        self.assertFalse(result["metrics"]["failed_tasks"]["available"])

    def test_rejects_invalid_threshold_and_snapshot(self):
        with self.assertRaises(ValueError):
            evaluate_improvement_snapshots([], {})
        for threshold in (True, 0, 10001, 1.5):
            with self.subTest(threshold=threshold), self.assertRaises(ValueError):
                evaluate_improvement_snapshots({}, {}, minimum_completed_observations=threshold)


if __name__ == "__main__":
    unittest.main()
