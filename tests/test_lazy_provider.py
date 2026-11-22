import threading
import unittest

from providers.lazy_provider import LazyProvider


class FakeProvider:
    def __init__(self):
        self.calls = []
        self.closed = 0

    def generate_chat(self, *, messages, tools, tool_choice):
        self.calls.append((messages, tools, tool_choice))
        return {"text": "ok", "tool_calls": {"tool_calls": []}}

    def generate_chat_stream(self, *, messages, tools, tool_choice):
        yield {"type": "text_delta", "text": "ok"}

    def close(self):
        self.closed += 1


class LazyProviderTests(unittest.TestCase):
    def test_construction_is_deferred_until_first_chat(self):
        config = {"provider": "openai"}
        calls = []
        provider = FakeProvider()
        lazy = LazyProvider(
            config, environ={}, provider_factory=lambda cfg, environ: calls.append(cfg["provider"]) or provider,
        )
        self.assertFalse(lazy.initialized)
        self.assertEqual(calls, [])
        result = lazy.generate_chat(messages=[{"role": "user", "content": "hi"}],
                                    tools=[], tool_choice="none")
        self.assertEqual(result["text"], "ok")
        self.assertEqual(calls, ["openai"])
        self.assertTrue(lazy.initialized)

    def test_persisted_provider_selection_is_observed_before_first_use(self):
        config = {"provider": "openai"}
        calls = []
        provider = FakeProvider()
        lazy = LazyProvider(
            config, environ={},
            provider_factory=lambda cfg, environ: calls.append(cfg["provider"]) or provider,
        )
        config["provider"] = "ollama"
        lazy.generate_chat(messages=[{"role": "user", "content": "hi"}], tools=[])
        self.assertEqual(calls, ["ollama"])

    def test_initialization_failure_is_sanitized_and_cached(self):
        calls = []
        def fail(config, environ):
            calls.append(True)
            raise RuntimeError("credential value and endpoint")
        lazy = LazyProvider({}, environ={}, provider_factory=fail)
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError, "^configured_provider_unavailable$"):
                lazy.generate_chat(messages=[], tools=[])
        self.assertEqual(calls, [True])

    def test_concurrent_first_use_constructs_provider_once(self):
        provider = FakeProvider()
        calls = []
        lazy = LazyProvider(
            {}, environ={}, provider_factory=lambda cfg, environ: (calls.append(True), provider)[1],
        )
        errors = []
        def invoke():
            try:
                lazy.generate_chat(messages=[{"role": "user", "content": "hi"}], tools=[])
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=invoke) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(calls, [True])
        self.assertEqual(len(provider.calls), 8)

    def test_streaming_is_deferred_until_stream_request(self):
        provider = FakeProvider()
        calls = []
        lazy = LazyProvider(
            {}, environ={}, provider_factory=lambda cfg, environ: calls.append(True) or provider,
        )
        self.assertEqual(calls, [])
        events = list(lazy.generate_chat_stream(messages=[], tools=[], tool_choice="none"))
        self.assertEqual(events[0]["text"], "ok")
        self.assertEqual(calls, [True])

    def test_close_without_use_does_not_construct_provider(self):
        calls = []
        lazy = LazyProvider({}, environ={}, provider_factory=lambda cfg, environ: calls.append(True))
        lazy.close()
        lazy.close()
        self.assertEqual(calls, [])
        with self.assertRaisesRegex(RuntimeError, "configured_provider_closed"):
            lazy.generate_chat(messages=[], tools=[])

    def test_failed_provider_close_can_be_retried(self):
        class RetryProvider(FakeProvider):
            def close(self):
                self.closed += 1
                if self.closed == 1:
                    raise OSError("temporary close failure")
        provider = RetryProvider()
        lazy = LazyProvider({}, environ={}, provider_factory=lambda cfg, environ: provider)
        lazy.generate_chat(messages=[{"role": "user", "content": "hi"}], tools=[])
        with self.assertRaises(OSError):
            lazy.close()
        lazy.close()
        lazy.close()
        self.assertEqual(provider.closed, 2)

    def test_close_releases_initialized_provider_once(self):
        provider = FakeProvider()
        lazy = LazyProvider({}, environ={}, provider_factory=lambda cfg, environ: provider)
        lazy.generate_chat(messages=[{"role": "user", "content": "hi"}], tools=[])
        lazy.close()
        lazy.close()
        self.assertEqual(provider.closed, 1)


if __name__ == "__main__":
    unittest.main()
