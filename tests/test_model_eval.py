import unittest
from evaluation.model_eval import EvalCase, ModelEvaluator

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

if __name__=="__main__": unittest.main()
