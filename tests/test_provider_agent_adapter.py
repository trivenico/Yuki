import unittest

from providers.agent_adapter import ProviderAgentAdapter


class FakeChatProvider:
    def __init__(self, response=None):
        self.response = response or {"text": "hello", "tool_calls": {"tool_calls": []}}
        self.received = None

    def generate_chat(self, *, messages, tools, tool_choice):
        self.received = (messages, tools, tool_choice)
        return self.response


class ProviderAgentAdapterTests(unittest.TestCase):
    def test_forwards_structured_messages_and_tool_definitions(self):
        provider = FakeChatProvider()
        adapter = ProviderAgentAdapter(provider)
        messages = [{"role": "system", "content": "You are Yuki"},
                    {"role": "user", "content": "Hello"}]
        tools = [{"type": "function", "function": {"name": "echo"}}]
        result = adapter(messages=messages, tools=tools, tool_choice="auto")
        self.assertEqual(result["text"], "hello")
        self.assertEqual(provider.received, (messages, tools, "auto"))

    def test_returns_tool_proposals_without_executing_them(self):
        provider = FakeChatProvider({"text": "", "tool_calls": {"tool_calls": [
            {"id": "c1", "name": "echo", "arguments": {"value": "x"}}
        ]}})
        result = ProviderAgentAdapter(provider)(
            messages=[{"role": "user", "content": "do"}], tools=[], tool_choice="auto")
        self.assertEqual(len(result["tool_calls"]["tool_calls"]), 1)

    def test_rejects_invalid_message_roles_and_shapes(self):
        adapter = ProviderAgentAdapter(FakeChatProvider())
        for messages in (
            [], [{"role": "developer", "content": "x"}],
            [{"role": "user", "content": "x", "extra": True}],
            [{"role": "user", "content": 3}],
            [{"role": [], "content": "x"}],
        ):
            with self.subTest(messages=messages), self.assertRaises(ValueError):
                adapter(messages=messages, tools=[])

    def test_enforces_transcript_and_tool_bounds(self):
        adapter = ProviderAgentAdapter(FakeChatProvider())
        with self.assertRaises(ValueError):
            adapter(messages=[{"role": "user", "content": "x" * 100_001}], tools=[])
        with self.assertRaises(ValueError):
            adapter(messages=[{"role": "user", "content": "x"}], tools=[{}] * 33)

    def test_rejects_unsupported_provider_envelopes(self):
        for response in (
            {"text": "x", "tool_calls": {}, "secret": "unexpected"},
            {"text": 3, "tool_calls": {}},
            {"text": "x", "tool_calls": []},
        ):
            with self.subTest(response=response), self.assertRaises(ValueError):
                ProviderAgentAdapter(FakeChatProvider(response))(
                    messages=[{"role": "user", "content": "x"}], tools=[])

    def test_requires_explicit_chat_provider_contract(self):
        with self.assertRaises(TypeError):
            ProviderAgentAdapter(object())


if __name__ == "__main__":
    unittest.main()
