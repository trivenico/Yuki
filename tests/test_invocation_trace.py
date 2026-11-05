import unittest
from datetime import datetime, timezone

from providers.invocation_trace import InvocationRecordingProviderAdapter


class FakeProvider:
    def __init__(self, failure=False):
        self.failure = failure

    def generate_chat(self, *, messages, tools, tool_choice):
        if self.failure:
            raise RuntimeError("secret prompt must never be recorded")
        return {"text": "private response", "tool_calls": {"tool_calls": []}}


class InvocationTraceTests(unittest.TestCase):
    def setUp(self):
        self.records = []
        self.adapter = InvocationRecordingProviderAdapter(
            FakeProvider(), provider_id="ollama", model_id="local-model:latest",
            record_sink=self.records.append, clock=lambda: 12.5,
            utc_now=lambda: datetime(2026, 11, 5, tzinfo=timezone.utc),
            invocation_id_factory=lambda: "invocation-1",
        )
        self.messages = [{"role": "user", "content": "private prompt"}]

    def test_success_records_versioned_metadata_without_content(self):
        result = self.adapter(messages=self.messages, tools=[])
        self.assertEqual(result["text"], "private response")
        record = self.records[0]
        self.assertEqual(record["schema_version"], 1)
        self.assertEqual(record["provider_id"], "ollama")
        self.assertEqual(record["model_id"], "local-model:latest")
        self.assertEqual(record["outcome"], "success")
        self.assertEqual(record["input_chars"], len("private prompt"))
        self.assertEqual(record["response_chars"], len("private response"))
        self.assertNotIn("private prompt", repr(record))
        self.assertNotIn("private response", repr(record))
        self.assertNotIn("error", record)

    def test_failure_records_exception_type_not_message(self):
        self.adapter.provider = FakeProvider(failure=True)
        with self.assertRaisesRegex(RuntimeError, "secret prompt"):
            self.adapter(messages=self.messages, tools=[])
        self.assertEqual(self.records[0]["outcome"], "error")
        self.assertEqual(self.records[0]["error_type"], "RuntimeError")
        self.assertNotIn("secret prompt", repr(self.records[0]))

    def test_rejects_invalid_provider_and_model_identity(self):
        with self.assertRaises(ValueError):
            InvocationRecordingProviderAdapter(
                FakeProvider(), provider_id="Ollama", model_id="m",
                record_sink=self.records.append,
            )
        with self.assertRaises(ValueError):
            InvocationRecordingProviderAdapter(
                FakeProvider(), provider_id="ollama", model_id="",
                record_sink=self.records.append,
            )

    def test_stream_records_only_metadata_after_completion(self):
        class StreamingProvider(FakeProvider):
            def generate_chat_stream(self, *, messages, tools, tool_choice):
                yield {"type": "text_delta", "text": "safe "}
                yield {"type": "text_delta", "text": "output"}
                yield {"type": "response", "response": {
                    "text": "safe output", "tool_calls": {"tool_calls": []}
                }}
        adapter = InvocationRecordingProviderAdapter(
            StreamingProvider(), provider_id="ollama", model_id="stream-model",
            record_sink=self.records.append,
            utc_now=lambda: datetime(2026, 11, 5, tzinfo=timezone.utc),
        )
        events = list(adapter.stream_model(messages=self.messages, tools=[]))
        self.assertEqual(events[-1]["type"], "response")
        self.assertEqual(self.records[0]["response_chars"], len("safe output"))
        self.assertNotIn("safe output", repr(self.records[0]))



    def test_early_stream_close_is_recorded_as_error(self):
        class StreamingProvider(FakeProvider):
            def generate_chat_stream(self, *, messages, tools, tool_choice):
                yield {"type": "text_delta", "text": "partial"}
                yield {"type": "response", "response": {
                    "text": "partial", "tool_calls": {"tool_calls": []}
                }}
        adapter = InvocationRecordingProviderAdapter(
            StreamingProvider(), provider_id="ollama", model_id="stream-model",
            record_sink=self.records.append,
            utc_now=lambda: datetime(2026, 11, 5, tzinfo=timezone.utc),
        )
        stream = adapter.stream_model(messages=self.messages, tools=[])
        next(stream)
        stream.close()
        self.assertEqual(self.records[0]["outcome"], "error")
        self.assertEqual(self.records[0]["error_type"], "StreamClosedEarly")

    def test_runtime_decorator_uses_owner_only_for_configuration(self):
        from providers.agent_adapter import ProviderAgentAdapter
        from providers.invocation_trace import create_invocation_trace_decorator
        decorate = create_invocation_trace_decorator(
            record_sink=self.records.append,
            provider_id_for_owner=lambda owner: "ollama",
            model_id_for_owner=lambda owner: "local-model:latest",
        )
        adapter = decorate("owner-secret", ProviderAgentAdapter(FakeProvider()))
        adapter(messages=self.messages, tools=[])
        self.assertEqual(self.records[0]["model_id"], "local-model:latest")
        self.assertNotIn("owner-secret", repr(self.records[0]))

if __name__ == "__main__":
    unittest.main()
