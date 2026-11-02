import json
import unittest
from unittest.mock import patch

from providers.openai_compatible import OpenAICompatibleProvider


class FakeResponse:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit):
        return self.body[:limit]


class FakeOpener:
    def __init__(self, body, requests):
        self.body = body
        self.requests = requests

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        return FakeResponse(self.body)


class OpenAICompatibleToolCallTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.provider = OpenAICompatibleProvider(
            "test-model",
            environ={"OPENAI_API_KEY": "test-secret"},
        )
        self.tools = [{
            "type": "function",
            "function": {
                "name": "lookup",
                "description": "Look up a safe test value",
                "parameters": {
                    "type": "object",
                    "properties": {"key": {"type": "string"}},
                    "required": ["key"],
                    "additionalProperties": False,
                },
                "strict": True,
            },
        }]

    def response(self, calls=None, content=None, finish="tool_calls"):
        return {"choices": [{
            "message": {
                "role": "assistant",
                "content": content,
                "tool_calls": calls or [],
            },
            "finish_reason": finish,
        }]}

    def invoke_with(self, body):
        opener = FakeOpener(body, self.requests)
        with patch("providers.openai_compatible.urllib.request.build_opener", return_value=opener):
            return self.provider.generate_with_tools("Find the value", self.tools)

    def test_native_call_is_normalized_to_yuki_envelope(self):
        body = self.response([{
            "id": "call_123",
            "type": "function",
            "function": {"name": "lookup", "arguments": '{"key":"value"}'},
        }])
        result = self.invoke_with(body)
        self.assertEqual(result["tool_calls"], {
            "tool_calls": [{"id": "call_123", "name": "lookup", "arguments": {"key": "value"}}]
        })
        self.assertEqual(result["finish_reason"], "tool_calls")
        request, timeout = self.requests[0]
        payload = json.loads(request.data)
        self.assertEqual(payload["tool_choice"], "auto")
        self.assertEqual(payload["tools"][0]["function"]["name"], "lookup")
        self.assertEqual(timeout, 45)
        self.assertEqual(request.get_header("Authorization"), "Bearer test-secret")

    def test_text_only_response_is_supported(self):
        result = self.invoke_with(self.response(content="No tool needed", finish="stop"))
        self.assertEqual(result["tool_calls"], {"tool_calls": []})
        self.assertEqual(result["text"], "No tool needed")

    def test_unadvertised_or_malformed_provider_calls_are_rejected(self):
        invalid = [
            [{"id": "call_1", "type": "function",
              "function": {"name": "unadvertised", "arguments": "{}"}}],
            [{"id": "call_1", "type": "function",
              "function": {"name": "lookup", "arguments": "[]"}}],
            [{"id": "call_1", "type": "function",
              "function": {"name": "lookup", "arguments": "not-json"}}],
            [{"id": "call_1", "type": "function",
              "function": {"name": "lookup", "arguments": '{"x":1}' }},
             {"id": "call_1", "type": "function",
              "function": {"name": "lookup", "arguments": '{"x":2}'}}],
        ]
        for calls in invalid:
            with self.subTest(calls=calls), self.assertRaises(RuntimeError):
                self.invoke_with(self.response(calls))

    def test_generate_chat_preserves_role_order_and_normalizes_tool_calls(self):
        body = self.response([{
            "id": "call_chat",
            "type": "function",
            "function": {"name": "lookup", "arguments": '{"key":"from-chat"}'},
        }], content="")
        opener = FakeOpener(body, self.requests)
        messages = [
            {"role": "system", "content": "You are Yuki"},
            {"role": "user", "content": "Find it"},
            {"role": "assistant", "content": "I will look it up"},
        ]
        with patch("providers.openai_compatible.urllib.request.build_opener", return_value=opener):
            result = self.provider.generate_chat(messages=messages, tools=self.tools)
        payload = json.loads(self.requests[0][0].data)
        self.assertEqual(payload["messages"], messages)
        self.assertEqual(result["tool_calls"]["tool_calls"][0]["arguments"], {"key": "from-chat"})
        self.assertEqual(result["text"], "")

    def test_generate_chat_rejects_bad_transcripts_before_network(self):
        invalid = [
            [],
            [{"role": "developer", "content": "no"}],
            [{"role": "user", "content": 4}],
            [{"role": "user", "content": "x", "extra": True}],
        ]
        for messages in invalid:
            with self.subTest(messages=messages), self.assertRaises(ValueError):
                self.provider.generate_chat(messages=messages, tools=self.tools)
        self.assertEqual(self.requests, [])

    def test_invalid_tool_definitions_fail_before_network(self):
        invalid_tools = [
            [],
            [{"type": "function", "function": {"name": "lookup", "parameters": []}}],
            self.tools + self.tools,
        ]
        for tools in invalid_tools:
            with self.subTest(tools=tools), self.assertRaises(ValueError):
                self.provider.generate_with_tools("prompt", tools)
        self.assertEqual(self.requests, [])

    def test_missing_credentials_fail_before_network(self):
        provider = OpenAICompatibleProvider("test-model", environ={})
        with self.assertRaises(RuntimeError):
            provider.generate_with_tools("prompt", self.tools)
        self.assertEqual(self.requests, [])


if __name__ == "__main__":
    unittest.main()
