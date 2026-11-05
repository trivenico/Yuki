import threading
import unittest

from providers.agent_adapter import ProviderAgentAdapter, ProviderResponseError
from providers.provider_execution import ProviderCancelled, ProviderDeadlineExceeded


TOOLS = [{"type": "function", "function": {
    "name": "lookup", "description": "Read-only lookup",
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
}}]
MESSAGES = [{"role": "user", "content": "hello"}]


class BehaviorProvider:
    def __init__(self, response=None, events=None):
        self.response = response if response is not None else {
            "text": "ok", "tool_calls": {"tool_calls": []}, "finish_reason": "stop"
        }
        self.events = events if events is not None else [
            {"type": "text_delta", "text": "hello"},
            {"type": "response", "response": self.response},
        ]

    def generate_chat(self, *, messages, tools, tool_choice):
        return self.response

    def generate_chat_stream(self, *, messages, tools, tool_choice):
        yield from self.events


class ProviderBehaviorContractTests(unittest.TestCase):
    def test_accepts_valid_normalized_response(self):
        result = ProviderAgentAdapter(BehaviorProvider())(messages=MESSAGES, tools=TOOLS)
        self.assertEqual(result["text"], "ok")
        self.assertEqual(result["finish_reason"], "stop")

    def test_rejects_malformed_tool_call_protocol(self):
        response = {"text": "", "tool_calls": {"tool_calls": [{
            "id": "call-1", "name": "lookup", "arguments": []
        }]}}
        with self.assertRaises(ProviderResponseError):
            ProviderAgentAdapter(BehaviorProvider(response))(messages=MESSAGES, tools=TOOLS)

    def test_rejects_tool_not_present_in_request(self):
        response = {"text": "", "tool_calls": {"tool_calls": [{
            "id": "call-1", "name": "delete_everything", "arguments": {}
        }]}}
        with self.assertRaises(ProviderResponseError):
            ProviderAgentAdapter(BehaviorProvider(response))(messages=MESSAGES, tools=TOOLS)

    def test_rejects_invalid_finish_reason_and_extra_envelope_fields(self):
        for response in (
            {"text": "ok", "tool_calls": {"tool_calls": []}, "finish_reason": []},
            {"text": "ok", "tool_calls": {"tool_calls": []}, "secret": "unexpected"},
        ):
            with self.subTest(response=response), self.assertRaises(ProviderResponseError):
                ProviderAgentAdapter(BehaviorProvider(response))(messages=MESSAGES, tools=TOOLS)

    def test_stream_events_and_final_response_are_validated(self):
        adapter = ProviderAgentAdapter(BehaviorProvider())
        events = list(adapter.stream_model(messages=MESSAGES, tools=TOOLS))
        self.assertEqual([item["type"] for item in events], ["text_delta", "response"])

    def test_stream_rejects_unknown_event_shape(self):
        provider = BehaviorProvider(events=[{"type": "tool_execution", "command": "run"}])
        with self.assertRaises(ProviderResponseError):
            list(ProviderAgentAdapter(provider).stream_model(messages=MESSAGES, tools=TOOLS))

    def test_stream_checks_cancellation_between_events(self):
        cancelled = threading.Event()
        def events():
            yield {"type": "text_delta", "text": "first"}
            cancelled.set()
            yield {"type": "text_delta", "text": "second"}
        provider = BehaviorProvider(events=events())
        iterator = ProviderAgentAdapter(provider).stream_model(
            messages=MESSAGES, tools=TOOLS, cancellation_event=cancelled,
        )
        self.assertEqual(next(iterator)["text"], "first")
        with self.assertRaises(ProviderCancelled):
            next(iterator)

    def test_stream_deadline_checked_between_events(self):
        import time
        def events():
            yield {"type": "text_delta", "text": "first"}
            time.sleep(0.02)
            yield {"type": "text_delta", "text": "second"}
        provider = BehaviorProvider(events=events())
        iterator = ProviderAgentAdapter(provider).stream_model(
            messages=MESSAGES, tools=TOOLS, timeout_seconds=0.01,
        )
        self.assertEqual(next(iterator)["text"], "first")
        with self.assertRaises(ProviderDeadlineExceeded):
            next(iterator)


if __name__ == "__main__":
    unittest.main()
