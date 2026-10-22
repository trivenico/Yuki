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

if __name__=="__main__": unittest.main()
