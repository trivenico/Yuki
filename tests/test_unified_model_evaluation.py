import unittest

from providers.unified_model_evaluation import (
    CandidateEvaluationError,
    comparable_reports,
    evaluate_candidate,
)


class UnifiedModelEvaluationTests(unittest.TestCase):
    def cases(self):
        return [
            {"id": "reasoning-1", "prompt": "Return alpha", "expected_terms": ["alpha"]},
            {"id": "reasoning-2", "prompt": "Return beta", "expected_terms": ["beta"]},
        ]

    def test_evaluates_local_ollama_candidate_without_api_credentials(self):
        report = evaluate_candidate(
            model_id="yuki-core:0.1",
            provider_id="ollama",
            runtime_id="ollama-local",
            benchmark_id="yuki-core-smoke",
            benchmark_version="1.0.0",
            cases=self.cases(),
            generate=lambda prompt: "alpha" if "alpha" in prompt else "beta",
            artifact_sha256="a" * 64,
        )
        self.assertEqual(report["provider_id"], "ollama")
        self.assertEqual(report["runtime_id"], "ollama-local")
        self.assertEqual(report["artifact_sha256"], "a" * 64)
        self.assertEqual(report["successful_cases"], 2)
        self.assertEqual(report["mean_expected_term_coverage"], 1.0)
        self.assertEqual(report["activation_policy"], "manual_review_required")

    def test_report_excludes_prompts_and_model_responses(self):
        report = evaluate_candidate(
            model_id="yuki-core:dev", provider_id="yuki", runtime_id="ollama",
            benchmark_id="privacy-smoke", benchmark_version="v1",
            cases=[{"id": "secret-case", "prompt": "private prompt sentinel",
                    "expected_terms": ["response sentinel"]}],
            generate=lambda _: "response sentinel",
        )
        serialized = repr(report)
        self.assertNotIn("private prompt sentinel", serialized)
        self.assertNotIn("response sentinel", serialized)
        self.assertIn("secret-case", serialized)
        self.assertEqual(report["results"][0]["matched_term_count"], 1)

    def test_provider_failure_is_redacted(self):
        def fail(_):
            raise RuntimeError("credential sentinel")
        report = evaluate_candidate(
            model_id="yuki-core", provider_id="ollama", runtime_id="local",
            benchmark_id="failure-smoke", benchmark_version="v1",
            cases=self.cases(), generate=fail,
        )
        self.assertEqual(report["failed_cases"], 2)
        self.assertNotIn("credential sentinel", repr(report))

    def test_same_benchmark_data_is_required_for_comparison(self):
        args = dict(
            model_id="candidate-a", provider_id="ollama", runtime_id="local",
            benchmark_id="smoke", benchmark_version="v1", cases=self.cases(),
            generate=lambda _: "alpha beta",
        )
        first = evaluate_candidate(**args)
        second = evaluate_candidate(**{**args, "model_id": "candidate-b"})
        self.assertTrue(comparable_reports(first, second))
        third = evaluate_candidate(**{**args, "benchmark_version": "v2"})
        self.assertFalse(comparable_reports(first, third))

    def test_invalid_identity_and_digest_are_rejected(self):
        args = dict(
            model_id="yuki-core", provider_id="ollama", runtime_id="local",
            benchmark_id="smoke", benchmark_version="v1", cases=self.cases(),
            generate=lambda _: "ok",
        )
        with self.assertRaises(CandidateEvaluationError):
            evaluate_candidate(**{**args, "model_id": "../escape"})
        with self.assertRaises(CandidateEvaluationError):
            evaluate_candidate(**{**args, "artifact_sha256": "not-a-digest"})

    def test_empty_expected_terms_require_manual_review_metric(self):
        report = evaluate_candidate(
            model_id="candidate", provider_id="mock", runtime_id="test",
            benchmark_id="smoke", benchmark_version="v1",
            cases=[{"id": "manual", "prompt": "test"}],
            generate=lambda _: "ok",
        )
        self.assertIsNone(report["mean_expected_term_coverage"])
        self.assertEqual(report["results"][0]["metric"], "manual_review_required")


if __name__ == "__main__":
    unittest.main()
