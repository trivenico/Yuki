from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from providers.openai_responses import OpenAIResponsesProvider


class FakeResponse:
    def __init__(self, body):
        self.body = json.dumps(body).encode()
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, size=-1):
        return self.body[:size]


class OpenAIResponsesProviderTests(unittest.TestCase):
    def provider(self, **kwargs):
        return OpenAIResponsesProvider(
            "gpt-6-astra", environ={"OPENAI_API_KEY": "test-key"},
            **kwargs,
        )

    def test_builds_responses_payload_and_extracts_text(self):
        response_body = {
            "status": "completed",
            "output": [
                {"type": "reasoning", "summary": []},
                {"type": "message", "content": [
                    {"type": "output_text", "text": "Verified "},
                    {"type": "output_text", "text": "answer."},
                ]},
            ],
        }
        with patch("urllib.request.build_opener") as build:
            build.return_value.open.return_value = FakeResponse(response_body)
            provider = self.provider(reasoning_effort="high")
            self.assertEqual(provider.generate("Review this change"), "Verified answer.")
            request = build.return_value.open.call_args.args[0]
            self.assertTrue(request.full_url.endswith("/v1/responses"))
            payload = json.loads(request.data)
            self.assertEqual(payload["model"], "gpt-6-astra")
            self.assertEqual(payload["reasoning"]["effort"], "high")
            self.assertEqual(payload["input"], "Review this change")
            self.assertNotIn("tools", payload)

    def test_requires_key_before_network(self):
        provider = OpenAIResponsesProvider("gpt-6-astra", environ={})
        self.assertFalse(provider.is_available())
        with self.assertRaisesRegex(RuntimeError, "OPENAI_API_KEY"):
            provider.generate("hello")

    def test_rejects_invalid_reasoning_effort(self):
        with self.assertRaises(ValueError):
            self.provider(reasoning_effort="none")

    def test_rejects_invalid_output_limit(self):
        with self.assertRaises(ValueError):
            self.provider(max_output_tokens=128001)

    def test_rejects_empty_prompt(self):
        with self.assertRaises(ValueError):
            self.provider().generate(" ")

    def test_rejects_incomplete_or_empty_response(self):
        with patch("urllib.request.build_opener") as build:
            build.return_value.open.return_value = FakeResponse({"status": "incomplete", "output": []})
            with self.assertRaisesRegex(RuntimeError, "did not complete"):
                self.provider().generate("hello")
        with patch("urllib.request.build_opener") as build:
            build.return_value.open.return_value = FakeResponse({"status": "completed", "output": []})
            with self.assertRaisesRegex(RuntimeError, "no text output"):
                self.provider().generate("hello")


if __name__ == "__main__":
    unittest.main()
