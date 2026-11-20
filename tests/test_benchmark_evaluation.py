import unittest
from dataclasses import replace

from evaluation.benchmark import (
    EvaluationError, evaluate_benchmark, validate_benchmark_report,
)


class BenchmarkEvaluationTests(unittest.TestCase):
    def observations(self):
        return [
            {"case_id": "case-1", "category": "reasoning", "expected_output": "42",
             "actual_output": "42", "latency_ms": 100, "cost_usd": 0.01,
             "safety_violation": False},
            {"case_id": "case-2", "category": "coding", "expected_output": "pass",
             "actual_output": "fail", "latency_ms": 300, "cost_usd": 0.02,
             "safety_violation": True},
            {"case_id": "case-3", "category": "safety", "expected_output": "refuse",
             "actual_output": "refuse", "latency_ms": 200, "cost_usd": 0.03,
             "safety_violation": False},
        ]

    def evaluate(self, rows=None):
        return evaluate_benchmark(
            self.observations() if rows is None else rows,
            benchmark_id="core-v1", model_id="local-model",
            model_version="2026.11", evaluated_at="2026-11-20T10:00:00Z",
        )

    def test_metrics_and_report_are_deterministic(self):
        first = self.evaluate()
        second = self.evaluate(list(reversed(self.observations())))
        self.assertEqual(first, second)
        self.assertEqual(first.total_cases, 3)
        self.assertEqual(first.exact_matches, 2)
        self.assertAlmostEqual(first.accuracy, 2 / 3)
        self.assertEqual(first.mean_latency_ms, 200)
        self.assertEqual(first.p95_latency_ms, 300)
        self.assertAlmostEqual(first.total_cost_usd, 0.06)
        self.assertEqual(first.safety_violations, 1)
        self.assertAlmostEqual(first.safety_violation_rate, 1 / 3)
        self.assertTrue(validate_benchmark_report(first))
        self.assertFalse(first.verified)

    def test_report_stores_digests_not_raw_output_text(self):
        report = self.evaluate()
        self.assertFalse(hasattr(report.cases[0], "actual_output"))
        self.assertEqual(len(report.cases[0].actual_output_sha256), 64)

    def test_tampering_with_metrics_or_cases_fails_validation(self):
        report = self.evaluate()
        self.assertFalse(validate_benchmark_report(replace(report, accuracy=1.0)))
        changed_case = replace(report.cases[0], latency_ms=999)
        self.assertFalse(validate_benchmark_report(replace(report, cases=(changed_case,) + report.cases[1:])))

    def test_rejects_invalid_cases_and_bounds(self):
        rows = self.observations()
        rows[0]["latency_ms"] = float("nan")
        with self.assertRaises(EvaluationError):
            self.evaluate(rows)
        rows = self.observations()
        rows[0]["safety_violation"] = 1
        with self.assertRaises(EvaluationError):
            self.evaluate(rows)
        rows = self.observations()
        rows.append(dict(rows[0]))
        with self.assertRaises(EvaluationError):
            self.evaluate(rows)
        with self.assertRaises(EvaluationError):
            self.evaluate([])

    def test_rejects_naive_timestamp_and_boolean_metrics(self):
        with self.assertRaises(EvaluationError):
            evaluate_benchmark(
                self.observations(), benchmark_id="core-v1", model_id="model",
                model_version="v1", evaluated_at="2026-11-20T10:00:00",
            )
        rows = self.observations()
        rows[0]["cost_usd"] = True
        with self.assertRaises(EvaluationError):
            self.evaluate(rows)


if __name__ == "__main__":
    unittest.main()
