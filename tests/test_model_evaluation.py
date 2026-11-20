import hashlib
import json
import unittest

from providers.model_evaluation import (
    EvaluationError,
    HuggingFaceChatClient,
    evaluate_model,
    score_response,
    validate_cases,
    validate_system_prompt,
)


class Response:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit=-1):
        return self.data[:limit]


class Opener:
    def __init__(self, data):
        self.data, self.request, self.timeout = data, None, None

    def open(self, request, timeout):
        self.request, self.timeout = request, timeout
        return Response(self.data)


class ModelEvaluationTests(unittest.TestCase):
    def test_validates_ids_prompts_and_terms(self):
        self.assertEqual(len(validate_cases([{"id": "a", "prompt": "test"}])), 1)
        for cases in [
            [{"id": "../unsafe", "prompt": "test"}],
            [{"id": "a", "prompt": "test"}, {"id": "a", "prompt": "other"}],
            [{"id": "a", "prompt": "x" * 9000}],
        ]:
            with self.assertRaises(ValueError):
                validate_cases(cases)

    def test_system_prompt_is_bounded_and_validated(self):
        self.assertEqual(validate_system_prompt("  You are Yuki.  "), "You are Yuki.")
        self.assertIsNone(validate_system_prompt(None))
        for invalid in ("", "x" * 16_001, "safe\x00unsafe", 123):
            with self.subTest(invalid=type(invalid).__name__):
                with self.assertRaises(ValueError):
                    validate_system_prompt(invalid)

    def test_latency_distribution_statistics(self):
        from providers.evaluation_stats import compute_stats
        metrics = compute_stats("latency_ms", [10.0, 20.0, 30.0, 40.0])
        self.assertEqual(metrics["mean_latency_ms"], 25.0)
        self.assertEqual(metrics["p50_latency_ms"], 25.0)
        self.assertEqual(metrics["p95_latency_ms"], 38.5)
        self.assertEqual(metrics["min_latency_ms"], 10.0)
        self.assertEqual(metrics["max_latency_ms"], 40.0)
        self.assertEqual(compute_stats("latency_ms", []), {})

    def test_expected_term_coverage_is_case_insensitive(self):
        result = score_response("Use Python and pytest", ["python", "pytest", "mypy"])
        self.assertEqual(result["score"], 2 / 3)
        self.assertEqual(result["missing_terms"], ["mypy"])
        self.assertIsNone(score_response("any", [])["score"])

    def test_evaluation_returns_metrics_and_never_auto_activates(self):
        report = evaluate_model("org/model", [
            {"id": "one", "prompt": "say alpha", "expected_terms": ["alpha"]},
            {"id": "two", "prompt": "say beta", "expected_terms": ["beta"]},
        ], lambda prompt: "alpha" if "alpha" in prompt else "beta")
        self.assertEqual(report["successful_cases"], 2)
        self.assertEqual(report["mean_expected_term_coverage"], 1)
        self.assertGreaterEqual(report["p50_latency_ms"], 0)
        self.assertGreaterEqual(report["p95_latency_ms"], report["p50_latency_ms"])
        self.assertEqual(report["activation_policy"], "manual_review_required")
        self.assertFalse(report["system_prompt_applied"])
        self.assertIsNone(report["system_prompt_sha256"])

    def test_evaluation_applies_same_system_prompt_to_every_case(self):
        seen = []

        def generate(user_prompt, system_prompt):
            seen.append((user_prompt, system_prompt))
            return "safe answer"

        system_prompt = "You are Yuki. Be concise and never invent actions."
        report = evaluate_model(
            "org/model",
            [{"id": "a", "prompt": "first"}, {"id": "b", "prompt": "second"}],
            generate,
            system_prompt=system_prompt,
        )
        self.assertEqual(seen, [("first", system_prompt), ("second", system_prompt)])
        self.assertTrue(report["system_prompt_applied"])
        self.assertEqual(
            report["system_prompt_sha256"],
            hashlib.sha256(system_prompt.encode("utf-8")).hexdigest(),
        )
        self.assertNotIn(system_prompt, json.dumps(report))

    def test_failed_generation_is_redacted(self):
        def fail(_):
            raise RuntimeError("secret token should not leak")
        report = evaluate_model("org/model", [{"id": "x", "prompt": "prompt"}], fail)
        self.assertEqual(report["failed_cases"], 1)
        self.assertNotIn("secret token", repr(report))

    def test_http_client_uses_fixed_endpoint_and_bounded_timeout(self):
        data = json.dumps({"choices": [{"message": {"content": "hello"}}]}).encode()
        opener = Opener(data)
        client = HuggingFaceChatClient("hf_test_token", opener=opener)
        self.assertEqual(client.generate("org/model", "hello"), "hello")
        self.assertEqual(opener.request.full_url, "https://router.huggingface.co/v1/chat/completions")
        self.assertNotIn("hf_test_token", opener.request.full_url)
        self.assertEqual(opener.request.get_header("Authorization"), "Bearer hf_test_token")
        self.assertEqual(opener.timeout, 20)

    def test_http_client_sends_system_role_before_user_prompt(self):
        data = json.dumps({"choices": [{"message": {"content": "hello"}}]}).encode()
        opener = Opener(data)
        client = HuggingFaceChatClient("hf_test_token", opener=opener)
        self.assertEqual(
            client.generate("org/model", "question", system_prompt="You are Yuki."),
            "hello",
        )
        payload = json.loads(opener.request.data.decode("utf-8"))
        self.assertEqual(payload["messages"], [
            {"role": "system", "content": "You are Yuki."},
            {"role": "user", "content": "question"},
        ])

    def test_invalid_model_and_token(self):
        with self.assertRaises(ValueError):
            HuggingFaceChatClient("")
        client = HuggingFaceChatClient("hf_test_token", opener=Opener(b"{}"))
        with self.assertRaises(ValueError):
            client.generate("../bad", "prompt")
        with self.assertRaises(ValueError):
            client.generate("org/model", "prompt", system_prompt="x" * 16_001)

    def test_malformed_response_fails_closed(self):
        client = HuggingFaceChatClient("hf_test_token", opener=Opener(b"not-json"))
        with self.assertRaises(EvaluationError):
            client.generate("org/model", "prompt")


if __name__ == "__main__":
    unittest.main()
