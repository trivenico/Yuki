import unittest

from providers.agent_adapter import ProviderAgentAdapter


class FakeUsageTracker:
    def __init__(self, fail=False):
        self.events = []
        self.fail = fail

    def record(self, **event):
        if self.fail:
            raise OSError("usage store unavailable")
        self.events.append(event)


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



    def test_stream_model_forwards_provider_native_events(self):
        class StreamingProvider(FakeChatProvider):
            def generate_chat_stream(self, *, messages, tools, tool_choice):
                self.received = (messages, tools, tool_choice)
                yield {"type": "text_delta", "text": "Hi"}
                yield {"type": "response", "response": {
                    "text": "Hi", "tool_calls": {"tool_calls": []}
                }}
        provider = StreamingProvider()
        adapter = ProviderAgentAdapter(provider)
        events = list(adapter.stream_model(
            messages=[{"role": "user", "content": "Hello"}], tools=[]
        ))
        self.assertEqual(events[0], {"type": "text_delta", "text": "Hi"})
        self.assertEqual(provider.received[0][0]["content"], "Hello")


    def test_records_exact_provider_usage(self):
        tracker = FakeUsageTracker()
        provider = FakeChatProvider({
            "text": "hello", "tool_calls": {"tool_calls": []},
            "usage": {"input_tokens": 17, "output_tokens": 9, "estimated": False},
        })
        result = ProviderAgentAdapter(provider, usage_tracker=tracker)(
            messages=[{"role": "user", "content": "hi"}], tools=[])
        self.assertEqual(result["text"], "hello")
        self.assertEqual(len(tracker.events), 1)
        self.assertEqual(tracker.events[0]["input_tokens"], 17)
        self.assertEqual(tracker.events[0]["output_tokens"], 9)
        self.assertFalse(tracker.events[0]["estimated"])

    def test_marks_fallback_character_estimates_as_estimated(self):
        tracker = FakeUsageTracker()
        adapter = ProviderAgentAdapter(FakeChatProvider(), usage_tracker=tracker)
        adapter(messages=[{"role": "user", "content": "12345678"}], tools=[])
        self.assertEqual(len(tracker.events), 1)
        self.assertTrue(tracker.events[0]["estimated"])
        self.assertGreaterEqual(tracker.events[0]["input_tokens"], 2)

    def test_usage_storage_failure_never_blocks_successful_inference(self):
        tracker = FakeUsageTracker(fail=True)
        adapter = ProviderAgentAdapter(FakeChatProvider(), usage_tracker=tracker)
        result = adapter(messages=[{"role": "user", "content": "hello"}], tools=[])
        self.assertEqual(result["text"], "hello")
        self.assertEqual(adapter.last_usage_error, "OSError")


    def test_usage_estimation_failure_does_not_block_inference(self):
        tracker = FakeUsageTracker()
        adapter = ProviderAgentAdapter(FakeChatProvider(), usage_tracker=tracker)
        result = adapter(
            messages=[{"role": "user", "content": "hello"}],
            tools=[{"not_json": object()}],
        )
        self.assertEqual(result["text"], "hello")
        self.assertEqual(adapter.last_usage_error, "TypeError")

    def test_streaming_records_usage_once_and_hides_internal_metadata(self):
        class StreamingProvider(FakeChatProvider):
            def generate_chat_stream(self, *, messages, tools, tool_choice):
                yield {"type": "text_delta", "text": "hello"}
                yield {"type": "response", "response": {
                    "text": "hello", "tool_calls": {"tool_calls": []},
                    "usage": {"input_tokens": 8, "output_tokens": 3, "estimated": False},
                }}
        tracker = FakeUsageTracker()
        events = list(ProviderAgentAdapter(StreamingProvider(), usage_tracker=tracker).stream_model(
            messages=[{"role": "user", "content": "hi"}], tools=[]))
        self.assertEqual(len(tracker.events), 1)
        self.assertEqual(tracker.events[0]["input_tokens"], 8)
        self.assertNotIn("usage", events[-1]["response"])

    def test_stream_model_fails_closed_when_provider_has_no_streaming(self):
        adapter = ProviderAgentAdapter(FakeChatProvider())
        with self.assertRaises(RuntimeError):
            list(adapter.stream_model(
                messages=[{"role": "user", "content": "Hello"}], tools=[]
            ))

if __name__ == "__main__":
    unittest.main()
