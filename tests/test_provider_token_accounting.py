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


if __name__ == "__main__":
    unittest.main()
