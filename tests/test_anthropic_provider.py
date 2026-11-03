import json
import unittest
from unittest.mock import patch

from providers.anthropic import AnthropicProvider
from providers.factory import create_provider
from providers.agent_adapter import ProviderAgentAdapter
from yuki_providers import ProviderManager


class FakeSSE:
    def __init__(self, events):
        self.lines = []
        for name, payload in events:
            self.lines.extend([
                ("event: " + name + "\n").encode(),
                ("data: " + json.dumps(payload) + "\n\n").encode(),
            ])
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def readline(self, limit):
        if not self.lines: return b""
        line = self.lines.pop(0)
        return line[:limit + 1] if len(line) > limit else line


class FakeOpener:
    def __init__(self, events, requests):
        self.response = FakeSSE(events)
        self.requests = requests
    def open(self, request, timeout):
        self.requests.append((request, timeout))
        return self.response


class AnthropicProviderTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.provider = AnthropicProvider(
            "claude-test", environ={"ANTHROPIC_API_KEY": "test-secret"}
        )
        self.tools = [{
            "type": "function",
            "function": {
                "name": "lookup", "description": "Look up a city",
                "parameters": {"type": "object", "properties": {
                    "city": {"type": "string"}}, "required": ["city"],
                    "additionalProperties": False},
            },
        }]

    def patch_stream(self, events):
        return patch(
            "providers.anthropic.urllib.request.build_opener",
            return_value=FakeOpener(events, self.requests),
        )

    def test_text_stream_is_normalized_and_authenticated(self):
        events = [
            ("message_start", {"type": "message_start", "message": {"id": "msg_1"}}),
            ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hello "}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Yuki"}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"}}),
            ("message_stop", {"type": "message_stop"}),
        ]
        with self.patch_stream(events):
            output = list(self.provider.generate_chat_stream(
                messages=[{"role": "system", "content": "Be helpful"},
                          {"role": "user", "content": "Hi"}],
                tools=self.tools,
            ))
        self.assertEqual(output[:2], [
            {"type": "text_delta", "text": "Hello "},
            {"type": "text_delta", "text": "Yuki"},
        ])
        self.assertEqual(output[-1]["response"]["text"], "Hello Yuki")
        request, _ = self.requests[0]
        payload = json.loads(request.data)
        self.assertEqual(payload["system"], "Be helpful")
        self.assertEqual(payload["tools"][0]["input_schema"], self.tools[0]["function"]["parameters"])
        self.assertEqual(request.get_header("X-api-key"), "test-secret")
        self.assertEqual(request.get_header("Anthropic-version"), "2023-06-01")

    def test_tool_use_json_fragments_become_guarded_tool_proposal(self):
        events = [
            ("message_start", {"type": "message_start", "message": {}}),
            ("content_block_start", {"type": "content_block_start", "index": 0,
                "content_block": {"type": "tool_use", "id": "toolu_123", "name": "lookup", "input": {}}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": '{"city":'}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": '"London"}'}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use"}}),
            ("message_stop", {"type": "message_stop"}),
        ]
        with self.patch_stream(events):
            output = list(self.provider.generate_chat_stream(
                messages=[{"role": "user", "content": "Look up London"}], tools=self.tools))
        self.assertEqual(output[-1]["response"]["tool_calls"]["tool_calls"], [{
            "id": "toolu_123", "name": "lookup", "arguments": {"city": "London"}
        }])

    def test_generate_chat_collects_stream_and_adapter_exposes_native_stream(self):
        events = [
            ("content_block_delta", {"type": "content_block_delta", "index": 0,
                "delta": {"type": "text_delta", "text": "Collected"}}),
            ("message_stop", {"type": "message_stop"}),
        ]
        with self.patch_stream(events):
            result = self.provider.generate_chat(messages=[{"role": "user", "content": "Hi"}], tools=[])
        self.assertEqual(result["text"], "Collected")
        self.assertTrue(callable(ProviderAgentAdapter(self.provider).stream_model))

    def test_incomplete_and_unadvertised_tool_streams_fail_closed(self):
        with self.patch_stream([("content_block_delta", {"type": "content_block_delta",
                "index": 0, "delta": {"type": "text_delta", "text": "partial"}})]):
            with self.assertRaisesRegex(RuntimeError, "message_stop"):
                list(self.provider.generate_chat_stream(
                    messages=[{"role": "user", "content": "Hi"}], tools=[]))
        events = [
            ("content_block_start", {"type": "content_block_start", "index": 0,
                "content_block": {"type": "tool_use", "id": "toolu_bad", "name": "unknown", "input": {}}}),
            ("message_stop", {"type": "message_stop"}),
        ]
        with self.patch_stream(events):
            with self.assertRaisesRegex(RuntimeError, "unrecognized"):
                list(self.provider.generate_chat_stream(
                    messages=[{"role": "user", "content": "Do it"}], tools=self.tools))

    def test_factory_and_manager_register_anthropic(self):
        provider = create_provider({"provider": "anthropic", "anthropic_model": "claude-test"},
                                   environ={"ANTHROPIC_API_KEY": "test-secret"})
        self.assertIsInstance(provider, AnthropicProvider)
        manager = ProviderManager(
            {"provider": "anthropic", "anthropic_model": "claude-test"},
            environ={"ANTHROPIC_API_KEY": "test-secret", "YUKI_ANTHROPIC_MODEL": "claude-test"},
        )
        self.assertEqual(manager.select("anthropic"), "Selected provider: anthropic")
        self.assertIsInstance(manager.create_selected_adapter(), AnthropicProvider)
        self.assertTrue(manager.create_selected_agent_model())

    def test_untrusted_endpoint_and_missing_credentials_are_rejected(self):
        with self.assertRaises(ValueError):
            AnthropicProvider("claude-test", base_url="https://example.com")
        provider = AnthropicProvider("claude-test", environ={})
        self.assertFalse(provider.is_available())
        with self.assertRaises(RuntimeError):
            list(provider.generate_chat_stream(
                messages=[{"role": "user", "content": "Hi"}], tools=[]))


if __name__ == "__main__":
    unittest.main()
