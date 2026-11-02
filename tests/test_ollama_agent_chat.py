import json
import unittest
from unittest.mock import patch

from providers.ollama import OllamaProvider


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, limit):
        return self.payload[:limit]


class FakeOpener:
    def __init__(self, payload, requests):
        self.payload = payload
        self.requests = requests

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        return FakeResponse(self.payload)


class OllamaAgentChatTests(unittest.TestCase):
    def setUp(self):
        self.provider = OllamaProvider(model="test-model")
        self.messages = [
            {"role": "system", "content": "You are Yuki"},
            {"role": "user", "content": "Find a value"},
        ]
        self.tools = [{
            "type": "function",
            "function": {
                "name": "lookup", "description": "Look up a value",
                "parameters": {"type": "object", "properties": {
                    "key": {"type": "string"}}, "required": ["key"],
                    "additionalProperties": False},
            },
        }]
        self.requests = []

    def test_preserves_roles_and_normalizes_tool_proposal_without_execution(self):
        fake = FakeOpener({"message": {"content": "", "tool_calls": [{
            "function": {"name": "lookup", "arguments": {"key": "value"}}
        }]}}, self.requests)
        with patch("providers.ollama.urllib.request.build_opener", return_value=fake):
            result = self.provider.generate_chat(messages=self.messages, tools=self.tools)
        request, timeout = self.requests[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, "http://127.0.0.1:11434/api/chat")
        self.assertEqual(payload["messages"], self.messages)
        self.assertEqual(result["tool_calls"]["tool_calls"][0]["name"], "lookup")
        self.assertEqual(result["tool_calls"]["tool_calls"][0]["arguments"], {"key": "value"})
        self.assertEqual(timeout, self.provider.timeout)

    def test_no_tool_choice_omits_tools(self):
        fake = FakeOpener({"message": {"content": "Hello", "tool_calls": []}}, self.requests)
        with patch("providers.ollama.urllib.request.build_opener", return_value=fake):
            result = self.provider.generate_chat(messages=self.messages, tools=self.tools, tool_choice="none")
        self.assertNotIn("tools", json.loads(self.requests[0][0].data))
        self.assertEqual(result["text"], "Hello")

    def test_invalid_transcripts_and_tool_definitions_fail_before_network(self):
        with self.assertRaises(ValueError):
            self.provider.generate_chat(messages=[{"role": [], "content": "x"}], tools=[])
        with self.assertRaises(ValueError):
            self.provider.generate_chat(messages=self.messages, tools=[{}] * 33)
        with self.assertRaises(ValueError):
            self.provider.generate_chat(messages=self.messages, tools=self.tools, tool_choice="sometimes")
        self.assertEqual(self.requests, [])

    def test_required_tool_choice_rejects_text_only_response(self):
        fake = FakeOpener({"message": {"content": "No tool", "tool_calls": []}}, self.requests)
        with patch("providers.ollama.urllib.request.build_opener", return_value=fake):
            with self.assertRaisesRegex(RuntimeError, "required tool"):
                self.provider.generate_chat(
                    messages=self.messages, tools=self.tools, tool_choice="required")

    def test_none_tool_choice_rejects_returned_tool_calls(self):
        fake = FakeOpener({"message": {"content": "", "tool_calls": [{
            "function": {"name": "lookup", "arguments": {"key": "value"}}
        }]}}, self.requests)
        with patch("providers.ollama.urllib.request.build_opener", return_value=fake):
            with self.assertRaisesRegex(RuntimeError, "tool_choice was none"):
                self.provider.generate_chat(
                    messages=self.messages, tools=self.tools, tool_choice="none")

    def test_rejects_unadvertised_tool_names(self):
        fake = FakeOpener({"message": {"content": "", "tool_calls": [{
            "function": {"name": "delete_everything", "arguments": {}}
        }]}}, self.requests)
        with patch("providers.ollama.urllib.request.build_opener", return_value=fake):
            with self.assertRaisesRegex(RuntimeError, "unrecognized"):
                self.provider.generate_chat(messages=self.messages, tools=self.tools)

    def test_rejects_malformed_tool_arguments(self):
        fake = FakeOpener({"message": {"content": "", "tool_calls": [{
            "function": {"name": "lookup", "arguments": "not-an-object"}
        }]}}, self.requests)
        with patch("providers.ollama.urllib.request.build_opener", return_value=fake):
            with self.assertRaisesRegex(RuntimeError, "malformed"):
                self.provider.generate_chat(messages=self.messages, tools=self.tools)


if __name__ == "__main__":
    unittest.main()
