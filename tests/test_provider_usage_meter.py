import unittest

from providers.usage_meter import UsageMeteredProvider, UsageMeteringError


class LedgerRecorder:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def record(self, **kwargs):
        if self.fail:
            raise OSError("database unavailable")
        self.calls.append(kwargs)
        return {"duplicate": False}


class ChatProvider:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def generate_chat(self, **kwargs):
        self.calls.append(kwargs)
        return self.response

    def generate_chat_stream(self, **kwargs):
        yield {"type": "text_delta", "text": "hello"}
        yield {"type": "response", "response": self.response}


class UsageMeterTests(unittest.TestCase):
    def setUp(self):
        self.ledger = LedgerRecorder()
        self.response = {
            "text": "hello", "tool_calls": {"tool_calls": []},
            "usage": {"input_tokens": 12, "output_tokens": 8, "estimated": False},
            "request_id": "provider-request-1",
        }
        self.provider = ChatProvider(self.response)
        self.meter = UsageMeteredProvider(
            self.provider, ledger=self.ledger, provider_id="openai",
            model_id="model-a", session_id="session-a", agent_id="planner",
            pricing_resolver=lambda provider, model: {
                "input_price_per_million": "0.10",
                "output_price_per_million": "0.40",
                "currency": "USD", "price_version": "catalog-v1",
            },
        )

    def test_records_provider_usage_and_versioned_price(self):
        result = self.meter.generate_chat(messages=[], tools=[])
        self.assertEqual(result, self.response)
        self.assertEqual(len(self.ledger.calls), 1)
        record = self.ledger.calls[0]
        self.assertEqual(record["request_id"], "provider-request-1")
        self.assertEqual((record["input_tokens"], record["output_tokens"]), (12, 8))
        self.assertEqual(record["price_version"], "catalog-v1")
        self.assertEqual(record["agent_id"], "planner")

    def test_legacy_generate_records_chat_usage_without_changing_return_type(self):
        result = self.meter.generate("hello")
        self.assertEqual(result, "hello")
        self.assertEqual(len(self.ledger.calls), 1)
        self.assertEqual(self.ledger.calls[0]["input_tokens"], 12)

    def test_availability_delegates_to_wrapped_provider(self):
        self.provider.is_available = lambda: True
        self.assertTrue(self.meter.is_available())

    def test_missing_usage_is_not_guessed(self):
        provider = ChatProvider({"text": "hello"})
        meter = UsageMeteredProvider(provider, ledger=self.ledger,
                                     provider_id="ollama", model_id="local")
        meter.generate_chat(messages=[], tools=[])
        self.assertEqual(self.ledger.calls, [])

    def test_stream_records_only_final_provider_usage(self):
        events = list(self.meter.generate_chat_stream(messages=[], tools=[]))
        self.assertEqual(events[0]["type"], "text_delta")
        self.assertEqual(len(self.ledger.calls), 1)

    def test_ledger_failure_is_explicit_after_provider_success(self):
        ledger = LedgerRecorder(fail=True)
        meter = UsageMeteredProvider(
            self.provider, ledger=ledger, provider_id="openai", model_id="model-a"
        )
        result = meter.generate_chat(messages=[], tools=[])
        self.assertEqual(result, self.response)
        self.assertEqual(meter.last_metering_error, "usage_persistence_failed")
        self.assertEqual(len(self.provider.calls), 1)

    def test_rejects_malformed_usage(self):
        provider = ChatProvider({
            "text": "hello",
            "usage": {"input_tokens": True, "output_tokens": 1, "estimated": False},
        })
        meter = UsageMeteredProvider(provider, ledger=self.ledger,
                                     provider_id="openai", model_id="model-a")
        result = meter.generate_chat(messages=[], tools=[])
        self.assertEqual(result, provider.response)
        self.assertEqual(meter.last_metering_error, "provider_usage_metadata_out_of_range")

    def test_estimated_usage_is_not_misrepresented_as_exact(self):
        provider = ChatProvider({
            "text": "hello",
            "usage": {"input_tokens": 10, "output_tokens": 5, "estimated": True},
        })
        meter = UsageMeteredProvider(provider, ledger=self.ledger,
                                     provider_id="openai", model_id="model-a")
        result = meter.generate_chat(messages=[], tools=[])
        self.assertEqual(result, provider.response)
        self.assertEqual(meter.last_metering_error, "estimated_provider_usage_not_supported")


if __name__ == "__main__":
    unittest.main()
