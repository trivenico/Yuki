import json
import os
import unittest
from unittest.mock import patch

from providers.ollama import OllamaProvider
from providers.openai_compatible import OpenAICompatibleProvider


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        return self.body[:size]


class FakeTracker:
    def __init__(self):
        self.events = []

    def record(self, **event):
        self.events.append(event)


class ProviderTokenAccountingTests(unittest.TestCase):
    def test_ollama_records_exact_usage_when_metadata_exists(self):
        tracker = FakeTracker()
        provider = OllamaProvider(model="local-model", usage_tracker=tracker)
        body = {"response": "answer", "prompt_eval_count": 12, "eval_count": 7}
        with patch("urllib.request.urlopen", return_value=FakeResponse(json.dumps(body).encode())):
            self.assertEqual(provider.generate("question"), "answer")
        self.assertEqual(len(tracker.events), 1)
        self.assertEqual(tracker.events[0]["input_tokens"], 12)
        self.assertEqual(tracker.events[0]["output_tokens"], 7)
        self.assertFalse(tracker.events[0]["estimated"])

    def test_ollama_marks_missing_usage_as_estimated(self):
        tracker = FakeTracker()
        provider = OllamaProvider(model="local-model", usage_tracker=tracker)
        with patch("urllib.request.urlopen", return_value=FakeResponse(
            json.dumps({"response": "answer"}).encode()
        )):
            provider.generate("question")
        self.assertTrue(tracker.events[0]["estimated"])

    def test_openai_compatible_records_exact_usage(self):
        tracker = FakeTracker()
        provider = OpenAICompatibleProvider("cloud-model", usage_tracker=tracker)
        body = {"choices": [{"message": {"content": "answer"}}],
                "usage": {"prompt_tokens": 19, "completion_tokens": 5}}
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
            with patch("urllib.request.urlopen", return_value=FakeResponse(json.dumps(body).encode())):
                self.assertEqual(provider.generate("question"), "answer")
        self.assertEqual(tracker.events[0]["input_tokens"], 19)
        self.assertEqual(tracker.events[0]["output_tokens"], 5)
        self.assertFalse(tracker.events[0]["estimated"])

    def test_openai_compatible_marks_missing_usage_as_estimated(self):
        tracker = FakeTracker()
        provider = OpenAICompatibleProvider("cloud-model", usage_tracker=tracker)
        body = {"choices": [{"message": {"content": "answer"}}]}
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
            with patch("urllib.request.urlopen", return_value=FakeResponse(json.dumps(body).encode())):
                provider.generate("question")
        self.assertTrue(tracker.events[0]["estimated"])

    def test_ollama_exposes_context_budget_from_provider_usage(self):
        tracker = FakeTracker()
        provider = OllamaProvider(
            model="local-model", usage_tracker=tracker, context_limit_tokens=8192
        )
        body = {"response": "answer", "prompt_eval_count": 12, "eval_count": 7}
        with patch("urllib.request.urlopen", return_value=FakeResponse(json.dumps(body).encode())):
            provider.generate("question")
        self.assertTrue(provider.last_context_budget["available"])
        self.assertEqual(provider.last_context_budget["used_tokens"], 19)
        self.assertEqual(provider.last_context_budget["remaining_tokens"], 8173)
        self.assertFalse(provider.last_context_budget["estimated"])

    def test_ollama_context_remains_unknown_without_explicit_limit(self):
        provider = OllamaProvider(model="local-model")
        body = {"response": "answer", "prompt_eval_count": 12, "eval_count": 7}
        with patch("urllib.request.urlopen", return_value=FakeResponse(json.dumps(body).encode())):
            provider.generate("question")
        self.assertFalse(provider.last_context_budget["available"])
        self.assertIsNone(provider.last_context_budget["remaining_tokens"])

    def test_openai_compatible_exposes_context_budget_from_provider_usage(self):
        provider = OpenAICompatibleProvider("cloud-model", context_limit_tokens=16384)
        body = {"choices": [{"message": {"content": "answer"}}],
                "usage": {"prompt_tokens": 19, "completion_tokens": 5}}
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
            with patch("urllib.request.urlopen", return_value=FakeResponse(json.dumps(body).encode())):
                provider.generate("question")
        self.assertTrue(provider.last_context_budget["available"])
        self.assertEqual(provider.last_context_budget["used_tokens"], 24)
        self.assertEqual(provider.last_context_budget["remaining_tokens"], 16360)
        self.assertFalse(provider.last_context_budget["estimated"])

    def test_provider_rejects_invalid_context_limit(self):
        for invalid in (True, 0, -1, 8192.0, "8192"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    OllamaProvider(model="local-model", context_limit_tokens=invalid)


if __name__ == "__main__":
    unittest.main()
