import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

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

    def test_stream_validation_rejects_unhashable_roles_and_tool_choice(self):
        adapter = ProviderAgentAdapter(FakeChatProvider())
        invalid_inputs = (
            ([{"role": [], "content": "x"}], [], "auto"),
            ([{"role": "user", "content": "x"}], [], []),
        )
        for messages, tools, tool_choice in invalid_inputs:
            with self.subTest(messages=messages, tool_choice=tool_choice), self.assertRaises(ValueError):
                adapter.stream_model(messages=messages, tools=tools, tool_choice=tool_choice)

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

    def test_stream_model_fails_closed_when_provider_has_no_streaming(self):
        adapter = ProviderAgentAdapter(FakeChatProvider())
        with self.assertRaises(RuntimeError):
            list(adapter.stream_model(
                messages=[{"role": "user", "content": "Hello"}], tools=[]
            ))


    def test_shared_provider_calls_are_serialized(self):
        entered = threading.Event()
        release = threading.Event()
        second_entered = threading.Event()
        active_lock = threading.Lock()
        active = 0
        maximum_active = 0

        class ConcurrentProvider(FakeChatProvider):
            def generate_chat(self, *, messages, tools, tool_choice):
                nonlocal active, maximum_active
                if messages[-1]["content"] == "second":
                    second_entered.set()
                with active_lock:
                    active += 1
                    maximum_active = max(maximum_active, active)
                    entered.set()
                try:
                    if messages[-1]["content"] == "first" and not release.wait(timeout=5):
                        raise TimeoutError("test did not release provider")
                    return {"text": messages[-1]["content"], "tool_calls": {"tool_calls": []}}
                finally:
                    with active_lock:
                        active -= 1

        adapter = ProviderAgentAdapter(ConcurrentProvider())
        def invoke(value):
            return adapter(
                messages=[{"role": "user", "content": value}], tools=[], tool_choice="auto"
            )["text"]

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(invoke, "first")
            self.assertTrue(entered.wait(timeout=3))
            second = pool.submit(invoke, "second")
            self.assertFalse(second_entered.wait(timeout=0.1))
            release.set()
            self.assertEqual(first.result(timeout=5), "first")
            self.assertEqual(second.result(timeout=5), "second")
        self.assertEqual(maximum_active, 1)
        self.assertEqual(active, 0)

    def test_provider_lock_covers_entire_stream_lifetime(self):
        entered = threading.Event()
        release = threading.Event()
        second_entered = threading.Event()

        class StreamingProvider(FakeChatProvider):
            def generate_chat_stream(self, *, messages, tools, tool_choice):
                if messages[-1]["content"] == "stream":
                    entered.set()
                    if not release.wait(timeout=5):
                        raise TimeoutError("test did not release stream")
                else:
                    second_entered.set()
                yield {"type": "text_delta", "text": "ok"}

            def generate_chat(self, *, messages, tools, tool_choice):
                second_entered.set()
                return {"text": "ok", "tool_calls": {"tool_calls": []}}

        adapter = ProviderAgentAdapter(StreamingProvider())
        with ThreadPoolExecutor(max_workers=2) as pool:
            stream = pool.submit(lambda: list(adapter.stream_model(
                messages=[{"role": "user", "content": "stream"}], tools=[]
            )))
            self.assertTrue(entered.wait(timeout=3))
            normal = pool.submit(lambda: adapter(
                messages=[{"role": "user", "content": "normal"}], tools=[]
            ))
            self.assertFalse(second_entered.wait(timeout=0.1))
            release.set()
            self.assertEqual(stream.result(timeout=5)[0]["text"], "ok")
            self.assertEqual(normal.result(timeout=5)["text"], "hello")
        self.assertTrue(second_entered.is_set())


    def test_provider_replacement_waits_for_inflight_request(self):
        entered = threading.Event()
        release = threading.Event()

        class BlockingProvider(FakeChatProvider):
            def generate_chat(self, *, messages, tools, tool_choice):
                entered.set()
                if not release.wait(timeout=5):
                    raise TimeoutError("test did not release provider")
                return {"text": "old", "tool_calls": {"tool_calls": []}}

        old = BlockingProvider()
        new = FakeChatProvider({"text": "new", "tool_calls": {"tool_calls": []}})
        adapter = ProviderAgentAdapter(old)
        with ThreadPoolExecutor(max_workers=2) as pool:
            request = pool.submit(adapter, messages=[{"role": "user", "content": "x"}], tools=[])
            self.assertTrue(entered.wait(timeout=3))
            swap = pool.submit(adapter.replace_provider, new)
            self.assertFalse(swap.done())
            release.set()
            self.assertEqual(request.result(timeout=5)["text"], "old")
            self.assertIs(swap.result(timeout=5), old)
        self.assertIs(adapter.provider, new)
        self.assertEqual(adapter(
            messages=[{"role": "user", "content": "x"}], tools=[]
        )["text"], "new")

if __name__ == "__main__":
    unittest.main()
