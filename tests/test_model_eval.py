import unittest
from evaluation.model_eval import EvalCase, ModelEvaluator\nfrom evaluation.benchmark_registry import BenchmarkDefinition, BenchmarkRegistry\n

class ModelEvaluatorTests(unittest.TestCase):
    def setUp(self): self.evaluator = ModelEvaluator(minimum_pass_rate=0.75)
    def test_required_terms_and_forbidden_terms(self):
        cases=[EvalCase("pass_1","coding","Say python",required_terms=("python",)),EvalCase("fail_1","coding","Avoid phrase",forbidden_terms=("secret",))]
        report=self.evaluator.run("test-model",lambda p:"Python is ready" if "Say" in p else "No details",cases)
        self.assertEqual(report.passed_count,2); self.assertEqual(report.pass_rate,1.0)
        self.assertIn("human_approval_required",report.promotion_reasons); self.assertFalse(report.promotion_eligible)
    def test_errors_are_redacted(self):
        report=self.evaluator.run("test-model",lambda p:(_ for _ in ()).throw(RuntimeError("secret token")),[EvalCase("err","general","private prompt")])
        self.assertFalse(report.results[0].passed); self.assertEqual(report.results[0].error_category,"provider_error:RuntimeError")
        self.assertNotIn("secret token",report.to_json()); self.assertNotIn("private prompt",report.to_json())
    def test_oversized_output_fails(self):
        report=self.evaluator.run("test-model",lambda p:"x"*100,[EvalCase("large","general","hi",max_response_chars=10)])
        self.assertEqual(report.results[0].error_category,"response_too_large")
    def test_limits_and_duplicate_ids(self):
        with self.assertRaises(ValueError): self.evaluator.run("x",lambda p:"ok",[])
        with self.assertRaises(ValueError): self.evaluator.run("x",lambda p:"ok",[EvalCase("same","a","1"),EvalCase("same","a","2")])
        with self.assertRaises(ValueError): ModelEvaluator(max_cases=500)
    def test_promotion_requires_all_gates(self):
        report=self.evaluator.run("test-model",lambda p:"ok",[EvalCase("case1","general","hi")],approval_required=False)
        rejected=self.evaluator.qualify(report,license_approved=True,security_review_passed=True,compatibility_passed=True,human_approved=False)
        self.assertFalse(rejected["eligible_for_staged_trial"])
        accepted=self.evaluator.qualify(report,license_approved=True,security_review_passed=True,compatibility_passed=True,human_approved=True)
        self.assertTrue(accepted["eligible_for_staged_trial"]); self.assertFalse(accepted["automatic_production_promotion"])


    def test_case_iterator_is_bounded_before_materialization(self):
        observed = []
        def unbounded_cases():
            for index in range(1000):
                observed.append(index)
                yield EvalCase(f"case-{index}", "general", "prompt")
        with self.assertRaises(ValueError):
            ModelEvaluator(max_cases=3).run("candidate", lambda prompt: "ok", unbounded_cases())
        self.assertEqual(observed, [0, 1, 2, 3])

    def test_rejects_invalid_case_limits_and_term_collections(self):
        invalid_cases = (
            EvalCase("bad", "general", "prompt", required_terms="abc"),
            EvalCase("bad", "general", "prompt", required_terms=("",)),
            EvalCase("bad", "general", "prompt", forbidden_terms=("secret", "SECRET")),
            EvalCase("bad", "general", "prompt", max_latency_seconds=True),
            EvalCase("bad", "general", "prompt", max_response_chars=True),
            EvalCase("bad", "general", ""),
        )
        for case in invalid_cases:
            with self.subTest(case=case):
                with self.assertRaises((TypeError, ValueError)):
                    self.evaluator.run("candidate", lambda prompt: "ok", [case])

    def test_rejects_non_finite_and_extreme_numeric_limits(self):
        for kwargs in (
            {"max_total_duration_seconds": float("nan")},
            {"max_total_duration_seconds": float("inf")},
            {"max_total_duration_seconds": 10 ** 1000},
            {"minimum_pass_rate": float("nan")},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    ModelEvaluator(**kwargs)
        for value in (float("nan"), float("inf"), 10 ** 1000):
            with self.subTest(value_type=type(value).__name__):
                case = EvalCase("numeric", "general", "prompt", max_latency_seconds=value)
                with self.assertRaises(ValueError):
                    self.evaluator.run("candidate", lambda prompt: "ok", [case])

    def test_rejects_truthy_non_boolean_approval_flags(self):
        with self.assertRaises(TypeError):
            self.evaluator.run("candidate", lambda prompt: "ok", [EvalCase("case", "general", "prompt")], approval_required="yes")

    def test_qualification_requires_explicit_boolean_gates_and_consistent_report(self):
        report = self.evaluator.run("candidate", lambda prompt: "ok", [EvalCase("case", "general", "prompt")], approval_required=False)
        with self.assertRaises(TypeError):
            self.evaluator.qualify(report, license_approved="yes", security_review_passed=True, compatibility_passed=True, human_approved=True)
        forged = type(report)(
            report.candidate_id, report.generated_at, report.case_count, 0, 1.0,
            report.total_duration_seconds, report.results, True, (),
        )
        result = self.evaluator.qualify(
            forged, license_approved=True, security_review_passed=True,
            compatibility_passed=True, human_approved=True,
        )
        self.assertFalse(result["eligible_for_staged_trial"])
        self.assertFalse(result["gates"]["evaluation_passed"])


class BenchmarkRegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = BenchmarkRegistry()
        self.case = EvalCase("case_1", "reasoning", "Explain a bounded retry.", required_terms=("retry",))
        self.definition = BenchmarkDefinition("core-reasoning", 1, "Core reasoning", (self.case,))

    def test_register_and_resolve_content_addressed_revision(self):
        registered = self.registry.register(self.definition)
        resolved = self.registry.resolve("core-reasoning", 1, expected_digest=registered.digest)
        self.assertEqual(resolved, registered)
        self.assertEqual(len(registered.digest), 64)
        self.assertEqual(self.registry.list_revisions("core-reasoning"), ((1, registered.digest),))

    def test_same_revision_is_idempotent_but_cannot_be_redefined(self):
        first = self.registry.register(self.definition)
        same = self.registry.register(self.definition)
        self.assertIs(first, same)
        changed = BenchmarkDefinition(
            "core-reasoning", 1, "Core reasoning", 
            (EvalCase("case_1", "reasoning", "Different prompt.", required_terms=("prompt",)),),
        )
        with self.assertRaisesRegex(ValueError, "immutable"):
            self.registry.register(changed)

    def test_revisions_are_sequential_and_immutable(self):
        self.registry.register(self.definition)
        revision_two = BenchmarkDefinition(
            "core-reasoning", 2, "Core reasoning v2",
            (EvalCase("case_2", "reasoning", "Explain bounded retries.", required_terms=("retries",)),),
        )
        self.registry.register(revision_two)
        with self.assertRaises(ValueError):
            self.registry.register(BenchmarkDefinition("core-reasoning", 4, "Gap", (self.case,)))

    def test_digest_mismatch_and_missing_revision_fail_closed(self):
        self.registry.register(self.definition)
        with self.assertRaises(ValueError):
            self.registry.resolve("core-reasoning", 1, expected_digest="0" * 64)
        with self.assertRaises(KeyError):
            self.registry.resolve("core-reasoning", 2)

    def test_rejects_invalid_and_duplicate_benchmark_cases(self):
        with self.assertRaises(ValueError):
            BenchmarkDefinition("core", 1, "Empty", ())
        with self.assertRaises(ValueError):
            BenchmarkDefinition("core", 1, "Duplicate", (self.case, self.case))
        with self.assertRaises(ValueError):
            BenchmarkDefinition("core", True, "Boolean version", (self.case,))
        with self.assertRaises(ValueError):
            BenchmarkDefinition("core", 1, "Oversized prompt", (EvalCase("large", "general", "x" * 8001),))

    def test_registry_capacity_is_enforced(self):
        registry = BenchmarkRegistry(max_revisions=1)
        registry.register(self.definition)
        with self.assertRaises(ValueError):
            registry.register(BenchmarkDefinition(
                "another", 1, "Another", (EvalCase("other", "general", "prompt"),)
            ))

if __name__=="__main__": unittest.main()
