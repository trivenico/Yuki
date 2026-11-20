import unittest
from dataclasses import replace

from evaluation.model_eval import CaseResult, EvalReport
from evaluation.comparison import ComparisonError, compare_evaluations, validate_comparison


def report(candidate, outcomes):
    results = tuple(
        CaseResult(case_id, "reasoning", passed, 0.1, 12, (), (), None)
        for case_id, passed in outcomes
    )
    passed = sum(item.passed for item in results)
    return EvalReport(candidate, "2026-11-20T10:00:00+00:00", len(results),
                      passed, round(passed / len(results), 4), 0.3, results,
                      False, ("human_approval_required",))


class ModelComparisonTests(unittest.TestCase):
    def test_same_cases_produce_deterministic_case_level_deltas(self):
        old = report("model-v1", [("a", True), ("b", False), ("c", True)])
        new = report("model-v2", [("a", False), ("b", True), ("c", True)])
        result = compare_evaluations(old, new, benchmark_id="core-v1",
                                     benchmark_digest="a" * 64)
        self.assertEqual(result.regressions, 1)
        self.assertEqual(result.improvements, 1)
        self.assertEqual(result.unchanged_passes, 1)
        self.assertEqual(result.unchanged_failures, 0)
        self.assertEqual(result.pass_rate_delta, 0.0)
        self.assertEqual([x.case_id for x in result.cases], ["a", "b", "c"])
        self.assertTrue(validate_comparison(result))
        self.assertFalse(result.verified)

    def test_rejects_different_case_sets_and_task_labels(self):
        with self.assertRaises(ComparisonError):
            compare_evaluations(report("old", [("a", True)]),
                                report("new", [("b", True)]),
                                benchmark_id="b", benchmark_digest="b" * 64)
        changed = EvalReport("new", "now", 1, 1, 1.0, 0.1,
                             (CaseResult("a", "different", True, 0.1, 1, (), (), None),),
                             False, ())
        with self.assertRaises(ComparisonError):
            compare_evaluations(report("old", [("a", True)]), changed,
                                benchmark_id="b", benchmark_digest="b" * 64)

    def test_rejects_inconsistent_reports_and_invalid_digest(self):
        broken = replace(report("old", [("a", True)]), pass_rate=0.0)
        with self.assertRaises(ComparisonError):
            compare_evaluations(broken, report("new", [("a", True)]),
                                benchmark_id="b", benchmark_digest="b" * 64)
        with self.assertRaises(ComparisonError):
            compare_evaluations(report("old", [("a", True)]),
                                report("new", [("a", True)]),
                                benchmark_id="b", benchmark_digest="not-a-digest")

    def test_detects_tampered_comparison(self):
        result = compare_evaluations(report("old", [("a", True), ("b", False)]),
                                     report("new", [("a", True), ("b", True)]),
                                     benchmark_id="b", benchmark_digest="c" * 64)
        self.assertFalse(validate_comparison(replace(result, improvements=0)))
        self.assertFalse(validate_comparison(replace(result, verified=True)))

    def test_rejects_non_boolean_case_states_and_inconsistent_statuses(self):
        result = compare_evaluations(report("old", [("a", True)]),
                                     report("new", [("a", False)]),
                                     benchmark_id="b", benchmark_digest="d" * 64)
        self.assertFalse(validate_comparison(replace(result, cases=(replace(result.cases[0], baseline_passed=1),))))
        self.assertFalse(validate_comparison(replace(result, cases=(replace(result.cases[0], status="improved"),))))

    def test_rejects_invalid_identity_and_aggregate_types(self):
        result = compare_evaluations(report("old", [("a", True)]),
                                     report("new", [("a", True)]),
                                     benchmark_id="b", benchmark_digest="e" * 64)
        self.assertFalse(validate_comparison(replace(result, candidate_id="")))
        self.assertFalse(validate_comparison(replace(result, regressions=True)))
        self.assertFalse(validate_comparison(replace(result, pass_rate_delta=float("nan"))))

    def test_rejects_malformed_report_metadata(self):
        valid = report("old", [("a", True)])
        with self.assertRaises(ComparisonError):
            compare_evaluations(replace(valid, promotion_eligible=1), report("new", [("a", True)]),
                                benchmark_id="b", benchmark_digest="f" * 64)
        with self.assertRaises(ComparisonError):
            compare_evaluations(replace(valid, generated_at=""), report("new", [("a", True)]),
                                benchmark_id="b", benchmark_digest="f" * 64)

if __name__ == "__main__":
    unittest.main()
