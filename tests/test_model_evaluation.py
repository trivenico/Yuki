import json
import unittest
from providers.model_evaluation import EvaluationError, HuggingFaceChatClient, evaluate_model, score_response, validate_cases

class Response:
    def __init__(self, data): self.data = data
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self, limit=-1): return self.data[:limit]

class Opener:
    def __init__(self, data): self.data, self.request, self.timeout = data, None, None
    def open(self, request, timeout):
        self.request, self.timeout = request, timeout
        return Response(self.data)

class ModelEvaluationTests(unittest.TestCase):
    def test_validates_ids_prompts_and_terms(self):
        self.assertEqual(len(validate_cases([{"id":"a","prompt":"test"}])), 1)
        for cases in [
            [{"id":"../unsafe","prompt":"test"}],
            [{"id":"a","prompt":"test"},{"id":"a","prompt":"other"}],
            [{"id":"a","prompt":"x"*9000}],
        ]:
            with self.assertRaises(ValueError): validate_cases(cases)

    def test_expected_term_coverage_is_case_insensitive(self):
        result = score_response("Use Python and pytest", ["python","pytest","mypy"])
        self.assertEqual(result["score"], 2/3)
        self.assertEqual(result["missing_terms"], ["mypy"])
        self.assertIsNone(score_response("any", [])["score"])

    def test_evaluation_returns_metrics_and_never_auto_activates(self):
        report = evaluate_model("org/model", [
            {"id":"one","prompt":"say alpha","expected_terms":["alpha"]},
            {"id":"two","prompt":"say beta","expected_terms":["beta"]},
        ], lambda prompt: "alpha" if "alpha" in prompt else "beta")
        self.assertEqual(report["successful_cases"], 2)
        self.assertEqual(report["mean_expected_term_coverage"], 1)
        self.assertEqual(report["activation_policy"], "manual_review_required")

    def test_failed_generation_is_redacted(self):
        def fail(_): raise RuntimeError("secret token should not leak")
        report = evaluate_model("org/model", [{"id":"x","prompt":"prompt"}], fail)
        self.assertEqual(report["failed_cases"], 1)
        self.assertNotIn("secret token", repr(report))

    def test_http_client_uses_fixed_endpoint_and_bounded_timeout(self):
        data = json.dumps({"choices":[{"message":{"content":"hello"}}]}).encode()
        opener = Opener(data)
        client = HuggingFaceChatClient("hf_test_token", opener=opener)
        self.assertEqual(client.generate("org/model","hello"), "hello")
        self.assertEqual(opener.request.full_url, "https://router.huggingface.co/v1/chat/completions")
        self.assertNotIn("hf_test_token", opener.request.full_url)
        self.assertEqual(opener.request.get_header("Authorization"), "Bearer hf_test_token")
        self.assertEqual(opener.timeout, 20)

    def test_invalid_model_and_token(self):
        with self.assertRaises(ValueError): HuggingFaceChatClient("")
        client = HuggingFaceChatClient("hf_test_token", opener=Opener(b"{}"))
        with self.assertRaises(ValueError): client.generate("../bad","prompt")

    def test_malformed_response_fails_closed(self):
        client = HuggingFaceChatClient("hf_test_token", opener=Opener(b"not-json"))
        with self.assertRaises(EvaluationError): client.generate("org/model","prompt")

if __name__ == "__main__": unittest.main()
