from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from providers.anthropic_messages import AnthropicMessagesProvider


class FakeResponse:
    def __init__(self, body):
        self.body = json.dumps(body).encode()
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, size=-1):
        return self.body[:size]


class AnthropicMessagesProviderTests(unittest.TestCase):
    def provider(self, **kwargs):
        return AnthropicMessagesProvider(
            "claude-fable-5-1", environ={"ANTHROPIC_API_KEY": "test-key"}, **kwargs
        )

    def test_builds_messages_payload_and_extracts_text(self):
        body = {"type": "message", "content": [
            {"type": "thinking", "thinking": "not exposed"},
            {"type": "text", "text": "Root cause "},
            {"type": "text", "text": "verified."},
        ]}
        with patch("urllib.request.build_opener") as build:
            build.return_value.open.return_value = FakeResponse(body)
            provider = self.provider(effort="xhigh")
            self.assertEqual(provider.generate("Review this code"), "Root cause verified.")
            request = build.return_value.open.call_args.args[0]
            self.assertTrue(request.full_url.endswith("/v1/messages"))
            payload = json.loads(request.data)
            self.assertEqual(payload["model"], "claude-fable-5-1")
            self.assertEqual(payload["output_config"]["effort"], "xhigh")
            self.assertEqual(payload["messages"][0]["content"], "Review this code")
            self.assertNotIn("tools", payload)
            self.assertEqual(request.get_header("Anthropic-version"), "2023-06-01")

    def test_requires_key_before_network(self):
        provider = AnthropicMessagesProvider("claude-fable-5-1", environ={})
        self.assertFalse(provider.is_available())
        with self.assertRaisesRegex(RuntimeError, "ANTHROPIC_API_KEY"):
            provider.generate("hello")

    def test_rejects_invalid_effort_and_limits(self):
        with self.assertRaises(ValueError):
            self.provider(effort="adaptive")
        with self.assertRaises(ValueError):
            self.provider(max_output_tokens=128001)

    def test_rejects_untrusted_endpoint(self):
        with self.assertRaisesRegex(ValueError, "trusted"):
            AnthropicMessagesProvider("claude-fable-5-1", base_url="https://attacker.example")
        with self.assertRaisesRegex(ValueError, "only be sent"):
            AnthropicMessagesProvider("claude-fable-5-1", base_url="https://api.openai.com/v1")

    def test_empty_prompt_and_incomplete_response_fail_closed(self):
        with self.assertRaises(ValueError):
            self.provider().generate(" ")
        with patch("urllib.request.build_opener") as build:
            build.return_value.open.return_value = FakeResponse({"type": "error", "error": {}})
            with self.assertRaisesRegex(RuntimeError, "unsupported or incomplete"):
                self.provider().generate("hello")


if __name__ == "__main__":
    unittest.main()
