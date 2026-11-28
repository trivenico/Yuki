import unittest
from providers.hybrid import HybridProvider
from providers.mock import MockProvider
from providers.ollama import OllamaProvider
from yuki_providers import ProviderManager

class ProviderRegistryRuntimeTests(unittest.TestCase):
    def test_registry_reports_only_factory_supported_integrations(self):
        manager = ProviderManager(environ={})
        entries = {item["name"]: item for item in manager.list_providers()}
        self.assertNotIn("mock", entries)
        self.assertTrue(entries["ollama"]["implemented"])
        self.assertTrue(entries["hybrid"]["implemented"])
        self.assertTrue(entries["openai"]["implemented"])
        self.assertFalse(entries["anthropic"]["implemented"])
        self.assertTrue(entries["gemini"]["implemented"])

    def test_default_openai_adapter_is_constructible_without_credentials(self):
        from providers.openai_compatible import OpenAICompatibleProvider
        adapter = ProviderManager(environ={}).create_selected_adapter()
        self.assertIsInstance(adapter, OpenAICompatibleProvider)
        self.assertFalse(adapter.is_available())

    def test_ollama_requires_configuration_and_failed_selection_is_atomic(self):
        manager = ProviderManager(environ={})
        message = manager.select("ollama")
        self.assertIn("Cannot select ollama", message)
        self.assertEqual(manager.current().name, "openai")

    def test_ollama_and_hybrid_use_shared_factory(self):
        manager = ProviderManager({"ollama_model": "test-model"}, environ={})
        self.assertEqual(manager.select("ollama"), "Selected provider: ollama")
        self.assertIsInstance(manager.create_selected_adapter(), OllamaProvider)
        self.assertEqual(manager.select("hybrid"), "Selected provider: hybrid")
        self.assertIsInstance(manager.create_selected_adapter(), HybridProvider)

    def test_selected_openai_provider_exposes_bounded_agent_model(self):
        from providers.agent_adapter import ProviderAgentAdapter
        manager = ProviderManager(environ={"OPENAI_API_KEY": "test-key"})
        model = manager.create_selected_agent_model()
        self.assertIsInstance(model, ProviderAgentAdapter)

    def test_selected_ollama_provider_exposes_bounded_agent_model(self):
        from providers.agent_adapter import ProviderAgentAdapter
        manager = ProviderManager(
            {"provider": "ollama", "ollama_model": "test-model"}, environ={}
        )
        model = manager.create_selected_agent_model()
        self.assertIsInstance(model, ProviderAgentAdapter)

    def test_text_only_hybrid_provider_fails_closed_for_agent_chat(self):
        manager = ProviderManager(
            {"provider": "hybrid", "ollama_model": "test-model"}, environ={}
        )
        with self.assertRaisesRegex(RuntimeError, "does not support structured agent chat"):
            manager.create_selected_agent_model()

    def test_unimplemented_provider_does_not_change_selection(self):
        manager = ProviderManager(environ={})
        message = manager.select("anthropic")
        self.assertIn("not implemented", message)
        self.assertEqual(manager.current().name, "openai")


    def test_routed_adapter_is_manager_owned_until_explicit_release(self):
        class CloseableProvider:
            def __init__(self):
                self.close_calls = 0
            def generate(self, prompt):
                return "ok"
            def is_available(self):
                return True
            def close(self):
                self.close_calls += 1

        class Route:
            provider = "custom"

        class Registry:
            def route(self, **kwargs):
                return Route()

        manager = ProviderManager(environ={})
        adapter = CloseableProvider()
        manager.register(type(manager.current())("custom", (), True), factory=lambda: adapter)
        manager.routing_registry = lambda: Registry()
        routed = manager.create_routed_adapter(preference=("custom",))
        self.assertIs(routed, adapter)
        self.assertEqual(adapter.close_calls, 0)
        manager.release_routed_adapter(routed)
        self.assertEqual(adapter.close_calls, 1)
        with self.assertRaisesRegex(ValueError, "not owned"):
            manager.release_routed_adapter(routed)

    def test_unavailable_routed_adapter_is_closed_before_error(self):
        class CloseableProvider:
            def __init__(self):
                self.close_calls = 0
            def generate(self, prompt):
                return "ok"
            def is_available(self):
                return False
            def close(self):
                self.close_calls += 1

        class Route:
            provider = "custom"

        class Registry:
            def route(self, **kwargs):
                return Route()

        manager = ProviderManager(environ={})
        adapter = CloseableProvider()
        manager.register(type(manager.current())("custom", (), True), factory=lambda: adapter)
        manager.routing_registry = lambda: Registry()
        with self.assertRaisesRegex(RuntimeError, "not available"):
            manager.create_routed_adapter(preference=("custom",))
        self.assertEqual(adapter.close_calls, 1)
        self.assertEqual(manager._routed_adapters, {})

    def test_provider_manager_close_releases_routed_adapters_and_retries_failures(self):
        class CloseableProvider:
            def __init__(self):
                self.close_calls = 0
                self.fail_once = True
            def generate(self, prompt):
                return "ok"
            def is_available(self):
                return True
            def close(self):
                self.close_calls += 1
                if self.fail_once:
                    self.fail_once = False
                    raise OSError("transient close failure")

        class Route:
            provider = "custom"

        class Registry:
            def route(self, **kwargs):
                return Route()

        manager = ProviderManager(environ={})
        adapter = CloseableProvider()
        manager.register(type(manager.current())("custom", (), True), factory=lambda: adapter)
        manager.routing_registry = lambda: Registry()
        manager.create_routed_adapter(preference=("custom",))
        with self.assertRaisesRegex(RuntimeError, "could not close"):
            manager.close()
        self.assertEqual(adapter.close_calls, 1)
        self.assertEqual(len(manager._routed_adapters), 1)
        manager.close()
        self.assertEqual(adapter.close_calls, 2)
        self.assertEqual(manager._routed_adapters, {})
        with self.assertRaisesRegex(RuntimeError, "closed"):
            manager.create_routed_adapter(preference=("custom",))


    def test_provider_selection_retains_replaced_adapters_until_manager_close(self):
        class CloseableProvider:
            def __init__(self):
                self.close_calls = 0
            def generate(self, prompt):
                return "ok"
            def is_available(self):
                return True
            def close(self):
                self.close_calls += 1

        manager = ProviderManager(environ={})
        first = CloseableProvider()
        second = CloseableProvider()
        manager.register(type(manager.current())("custom-a", (), True), factory=lambda: first)
        manager.register(type(manager.current())("custom-b", (), True), factory=lambda: second)
        self.assertEqual(manager.select("custom-a"), "Selected provider: custom-a")
        self.assertEqual(manager.select("custom-b"), "Selected provider: custom-b")
        self.assertEqual(first.close_calls, 0)
        manager.close()
        self.assertEqual(first.close_calls, 1)
        self.assertEqual(second.close_calls, 1)


    def test_provider_selection_serializes_factory_construction_and_publication(self):
        from threading import Event, Thread

        first_started = Event()
        release_first = Event()
        first = MockProvider()
        second = MockProvider()

        def slow_first():
            first_started.set()
            if not release_first.wait(2):
                raise TimeoutError("test did not release first factory")
            return first

        manager = ProviderManager(environ={})
        info_type = type(manager.current())
        manager.register(info_type("slow", (), True), factory=slow_first)
        manager.register(info_type("fast", (), True), factory=lambda: second)
        results = []
        first_thread = Thread(target=lambda: results.append(manager.select("slow")))
        second_thread = Thread(target=lambda: results.append(manager.select("fast")))
        first_thread.start()
        self.assertTrue(first_started.wait(2))
        second_thread.start()
        # The second selection cannot publish while the first selection owns
        # the lifecycle lock, so request order cannot be inverted by build time.
        release_first.set()
        first_thread.join(2)
        second_thread.join(2)
        self.assertFalse(first_thread.is_alive())
        self.assertFalse(second_thread.is_alive())
        self.assertEqual(manager.current().name, "fast")
        self.assertIs(manager.create_selected_adapter(), second)
        manager.close()


    def test_provider_manager_close_waits_for_request_lease_and_retries(self):
        manager = ProviderManager(environ={})
        adapter = manager.create_selected_adapter()
        with manager.lease_adapter(adapter):
            with self.assertRaisesRegex(RuntimeError, "active adapter lease"):
                manager.close()
            with self.assertRaisesRegex(RuntimeError, "closing or closed"):
                manager.lease_adapter(adapter)
            with self.assertRaisesRegex(RuntimeError, "closing or closed"):
                manager.select("openai")
        manager.close()
        with self.assertRaisesRegex(RuntimeError, "closing or closed"):
            manager.create_selected_adapter()

    def test_routed_adapter_cannot_be_released_while_request_is_leased(self):
        class CloseableProvider:
            def __init__(self):
                self.close_calls = 0
            def generate(self, prompt):
                return "ok"
            def is_available(self):
                return True
            def close(self):
                self.close_calls += 1

        class Route:
            provider = "custom"

        class Registry:
            def route(self, **kwargs):
                return Route()

        manager = ProviderManager(environ={})
        adapter = CloseableProvider()
        manager.register(type(manager.current())("custom", (), True), factory=lambda: adapter)
        manager.routing_registry = lambda: Registry()
        routed = manager.create_routed_adapter(preference=("custom",))
        with manager.lease_adapter(routed):
            with self.assertRaisesRegex(RuntimeError, "in use"):
                manager.release_routed_adapter(routed)
            with self.assertRaisesRegex(RuntimeError, "active adapter lease"):
                manager.close()
        manager.release_routed_adapter(routed)
        self.assertEqual(adapter.close_calls, 1)


    def test_selected_agent_model_uses_manager_lease_for_chat_and_shutdown(self):
        class ChatProvider:
            def __init__(self):
                self.close_calls = 0
                self.manager = None
            def generate(self, prompt):
                return prompt
            def is_available(self):
                return True
            def generate_chat(self, *, messages, tools, tool_choice):
                try:
                    self.manager.close()
                except RuntimeError as exc:
                    if "active adapter lease" not in str(exc):
                        raise
                else:
                    raise AssertionError("manager closed during active chat request")
                return {"text": "chat result", "tool_calls": {"tool_calls": []}}
            def close(self):
                self.close_calls += 1

        manager = ProviderManager(environ={})
        provider = ChatProvider()
        provider.manager = manager
        manager.register(type(manager.current())("chat-test", (), True), factory=lambda: provider)
        self.assertEqual(manager.select("chat-test"), "Selected provider: chat-test")
        model = manager.create_selected_agent_model()
        self.assertIs(model.provider_manager, manager)
        result = model(
            messages=[{"role": "user", "content": "hello"}],
            tools=[], tool_choice="none",
        )
        self.assertEqual(result["text"], "chat result")
        manager.close()
        self.assertEqual(provider.close_calls, 1)

    def test_selected_agent_model_stream_holds_lease_until_generator_closes(self):
        class StreamingChatProvider:
            def __init__(self):
                self.close_calls = 0
            def generate(self, prompt):
                return prompt
            def is_available(self):
                return True
            def generate_chat(self, **kwargs):
                return {"text": "unused", "tool_calls": {"tool_calls": []}}
            def generate_chat_stream(self, **kwargs):
                yield {"type": "token", "text": "one"}
                yield {"type": "token", "text": "two"}
            def close(self):
                self.close_calls += 1

        manager = ProviderManager(environ={})
        provider = StreamingChatProvider()
        manager.register(type(manager.current())("stream-test", (), True), factory=lambda: provider)
        manager.select("stream-test")
        model = manager.create_selected_agent_model()
        stream = model.stream_model(
            messages=[{"role": "user", "content": "hello"}],
            tools=[], tool_choice="none",
        )
        self.assertEqual(next(stream)["text"], "one")
        with self.assertRaisesRegex(RuntimeError, "active adapter lease"):
            manager.close()
        stream.close()
        manager.close()
        self.assertEqual(provider.close_calls, 1)

    def test_register_rejects_mutation_after_manager_close(self):
        manager = ProviderManager(environ={})
        manager.close()
        with self.assertRaisesRegex(RuntimeError, "closed"):
            manager.register(type(manager.current())("custom", (), True), factory=lambda: MockProvider())

    def test_custom_factory_must_return_provider_contract(self):
        manager = ProviderManager(environ={})
        manager.register(type(manager.current())("custom", (), True), factory=lambda: object())
        with self.assertRaisesRegex(TypeError, "generate"):
            manager._build_adapter("custom")

if __name__ == "__main__":
    unittest.main()
