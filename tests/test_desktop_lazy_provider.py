import threading
import unittest

from ui.desktop.lazy_provider import LazyDesktopProvider


class FakeProvider:
    def __init__(self):
        self.calls = []
        self.closed = 0

    def generate_chat(self, *, messages, tools, tool_choice):
        self.calls.append((messages, tools, tool_choice))
        return {"text": "answer", "tool_calls": {"tool_calls": []}}

    def close(self):
        self.closed += 1


class LazyDesktopProviderTests(unittest.TestCase):
    def test_construction_is_deferred_and_structured_contract_is_preserved(self):
        provider = FakeProvider()
        calls = []
        adapter = LazyDesktopProvider({}, lambda config: calls.append(config) or provider)
        self.assertEqual(calls, [])
        result = adapter.generate_chat(
            messages=[{"role": "user", "content": "hello"}],
            tools=[], tool_choice="none",
        )
        self.assertEqual(result["text"], "answer")
        self.assertEqual(len(calls), 1)
        self.assertEqual(provider.calls[0][1:], ([], "none"))

    def test_legacy_contract_is_adapted_without_tools(self):
        provider = FakeProvider()
        adapter = LazyDesktopProvider({}, lambda config: provider)
        answer = adapter.generate_chat(
            "You are Yuki.",
            [{"role": "user", "content": "hello"}],
        )
        self.assertEqual(answer, "answer")
        messages, tools, choice = provider.calls[0]
        self.assertEqual(messages[0], {"role": "system", "content": "You are Yuki."})
        self.assertEqual(tools, [])
        self.assertEqual(choice, "none")

    def test_invalid_legacy_request_does_not_initialize_provider(self):
        calls = []
        adapter = LazyDesktopProvider({}, lambda config: calls.append(True) or FakeProvider())
        with self.assertRaisesRegex(ValueError, "legacy chat message is invalid"):
            adapter.generate_chat("system", [{"role": "user", "content": "hello", "extra": True}])
        self.assertEqual(calls, [])
        self.assertFalse(adapter.initialized)

    def test_initialization_failure_is_sanitized_and_cached(self):
        calls = []
        def fail(config):
            calls.append(True)
            raise RuntimeError("secret endpoint and credential details")
        adapter = LazyDesktopProvider({}, fail)
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError, "^configured_provider_unavailable$"):
                adapter.generate_chat(messages=[], tools=[], tool_choice="none")
        self.assertEqual(len(calls), 1)

    def test_concurrent_first_use_initializes_once(self):
        provider = FakeProvider()
        calls = []
        adapter = LazyDesktopProvider(
            {}, lambda config: (calls.append(True), provider)[1],
        )
        failures = []
        def invoke():
            try:
                adapter.generate_chat(messages=[{"role": "user", "content": "x"}],
                                      tools=[], tool_choice="none")
            except Exception as exc:
                failures.append(exc)
        threads = [threading.Thread(target=invoke) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(failures, [])
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(provider.calls), 8)

    def test_failed_provider_close_is_retained_for_lifecycle_retry(self):
        class RetryProvider(FakeProvider):
            def close(self):
                self.closed += 1
                if self.closed == 1:
                    raise OSError("temporary close failure")
        provider = RetryProvider()
        adapter = LazyDesktopProvider({}, lambda config: provider)
        adapter.generate_chat(messages=[{"role": "user", "content": "x"}],
                              tools=[], tool_choice="none")
        with self.assertRaises(OSError):
            adapter.close()
        adapter.close()
        self.assertEqual(provider.closed, 2)
        adapter.close()
        self.assertEqual(provider.closed, 2)

    def test_close_is_idempotent_and_closes_only_initialized_provider(self):
        provider = FakeProvider()
        adapter = LazyDesktopProvider({}, lambda config: provider)
        adapter.close()
        adapter.close()
        self.assertEqual(provider.closed, 0)
        with self.assertRaisesRegex(RuntimeError, "desktop_provider_closed"):
            adapter.generate_chat(messages=[], tools=[], tool_choice="none")

        second = LazyDesktopProvider({}, lambda config: provider)
        second.generate_chat(messages=[{"role": "user", "content": "x"}],
                             tools=[], tool_choice="none")
        second.close()
        second.close()
        self.assertEqual(provider.closed, 1)


if __name__ == "__main__":
    unittest.main()
