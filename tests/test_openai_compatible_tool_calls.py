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



class FakeStreamResponse:
    def __init__(self, lines):
        self.lines = [line if isinstance(line, bytes) else line.encode() for line in lines]
        self.closed = False
    def __enter__(self): return self
    def __exit__(self, *args):
        self.closed = True
        return False
    def readline(self, limit):
        if not self.lines: return b""
        line = self.lines.pop(0)
        return line[:limit + 1] if len(line) > limit else line

class FakeStreamOpener:
    def __init__(self, lines, requests):
        self.response = FakeStreamResponse(lines)
        self.requests = requests
    def open(self, request, timeout):
        self.requests.append((request, timeout))
        return self.response

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
            [{"role": [], "content": "x"}],
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



    def test_non_streaming_generate_chat_enforces_tool_choice(self):
        invalid = [
            (self.response([{"id": "call_none", "type": "function",
                "function": {"name": "lookup", "arguments": "{\"key\":\"x\"}"}}]),
             "none", "tool_choice was none"),
            (self.response(content="No tool", finish="stop"),
             "required", "required tool"),
        ]
        for body, choice, message in invalid:
            opener = FakeOpener(body, self.requests)
            with self.subTest(choice=choice), patch(
                "providers.openai_compatible.urllib.request.build_opener", return_value=opener
            ), self.assertRaisesRegex(RuntimeError, message):
                self.provider.generate_chat(
                    messages=[{"role": "user", "content": "Find"}],
                    tools=self.tools, tool_choice=choice)

    def test_non_streaming_generate_with_tools_enforces_tool_choice(self):
        invalid = [
            (self.response([{"id": "call_none", "type": "function",
                "function": {"name": "lookup", "arguments": "{\"key\":\"x\"}"}}]),
             "none", "tool_choice was none"),
            (self.response(content="No tool", finish="stop"),
             "required", "required tool"),
        ]
        for body, choice, message in invalid:
            opener = FakeOpener(body, self.requests)
            with self.subTest(choice=choice), patch(
                "providers.openai_compatible.urllib.request.build_opener", return_value=opener
            ), self.assertRaisesRegex(RuntimeError, message):
                self.provider.generate_with_tools("Find", self.tools, tool_choice=choice)

    def test_native_sse_stream_emits_text_and_final_response(self):
        chunks = [
            {"choices": [{"delta": {"content": "Hello "}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": "Yuki"}, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ]
        lines = [("data: " + json.dumps(item) + "\n\n") for item in chunks] + ["data: [DONE]\n\n"]
        opener = FakeStreamOpener(lines, self.requests)
        with patch("providers.openai_compatible.urllib.request.build_opener", return_value=opener):
            events = list(self.provider.generate_chat_stream(messages=[{"role": "user", "content": "Hi"}], tools=self.tools))
        self.assertEqual(events[:2], [{"type": "text_delta", "text": "Hello "}, {"type": "text_delta", "text": "Yuki"}])
        self.assertEqual(events[-1]["response"]["text"], "Hello Yuki")
        self.assertTrue(json.loads(self.requests[0][0].data)["stream"])

    def test_native_sse_stream_reassembles_tool_call_fragments(self):
        chunks = [
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_stream", "function": {"name": "lookup", "arguments": "{\"key\":"}}]}, "finish_reason": None}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "\"value\"}"}}]}, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ]
        lines = [("data: " + json.dumps(item) + "\n") for item in chunks] + ["data: [DONE]\n"]
        opener = FakeStreamOpener(lines, self.requests)
        with patch("providers.openai_compatible.urllib.request.build_opener", return_value=opener):
            events = list(self.provider.generate_chat_stream(messages=[{"role": "user", "content": "Find"}], tools=self.tools))
        self.assertEqual(events[-1]["response"]["tool_calls"]["tool_calls"], [
            {"id": "call_stream", "name": "lookup", "arguments": {"key": "value"}}
        ])

    def test_native_sse_stream_rejects_unadvertised_tool(self):
        chunk = {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "bad", "function": {"name": "unadvertised", "arguments": "{}"}}
        ]}, "finish_reason": "tool_calls"}]}
        opener = FakeStreamOpener(["data: " + json.dumps(chunk) + "\n", "data: [DONE]\n"], self.requests)
        with patch("providers.openai_compatible.urllib.request.build_opener", return_value=opener):
            with self.assertRaises(RuntimeError):
                list(self.provider.generate_chat_stream(messages=[{"role": "user", "content": "Find"}], tools=self.tools))

    def test_sse_tool_choice_none_rejects_returned_tool_calls(self):
        chunk = {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_none", "function": {"name": "lookup", "arguments": "{\"key\":\"x\"}"}}
        ]}, "finish_reason": "tool_calls"}]}
        opener = FakeStreamOpener(["data: " + json.dumps(chunk) + "\n", "data: [DONE]\n"], self.requests)
        with patch("providers.openai_compatible.urllib.request.build_opener", return_value=opener):
            with self.assertRaisesRegex(RuntimeError, "tool_choice was none"):
                list(self.provider.generate_chat_stream(
                    messages=[{"role": "user", "content": "Find"}], tools=self.tools, tool_choice="none"))

    def test_sse_tool_choice_required_rejects_text_only_response(self):
        chunks = [
            {"choices": [{"delta": {"content": "No tool"}, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ]
        lines = ["data: " + json.dumps(item) + "\n" for item in chunks] + ["data: [DONE]\n"]
        opener = FakeStreamOpener(lines, self.requests)
        with patch("providers.openai_compatible.urllib.request.build_opener", return_value=opener):
            with self.assertRaisesRegex(RuntimeError, "required tool"):
                list(self.provider.generate_chat_stream(
                    messages=[{"role": "user", "content": "Find"}], tools=self.tools, tool_choice="required"))

    def test_closing_consumer_closes_underlying_sse_response(self):
        opener = FakeStreamOpener([
            "data: " + json.dumps({"choices": [{"delta": {"content": "first"}, "finish_reason": None}]}) + "\n",
            "data: " + json.dumps({"choices": [{"delta": {"content": "second"}, "finish_reason": None}]}) + "\n",
        ], self.requests)
        with patch("providers.openai_compatible.urllib.request.build_opener", return_value=opener):
            stream = self.provider.generate_chat_stream(messages=[{"role": "user", "content": "Hi"}], tools=self.tools)
            self.assertEqual(next(stream), {"type": "text_delta", "text": "first"})
            stream.close()
        self.assertTrue(opener.response.closed)
        self.assertEqual(self.requests[0][1], self.provider.timeout)

if __name__ == "__main__":
    unittest.main()
