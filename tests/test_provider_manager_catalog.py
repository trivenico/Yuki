import unittest
from yuki_providers import ProviderManager

class ProviderManagerCatalogTests(unittest.TestCase):
    def test_manager_exposes_catalog_without_secrets(self):
        secret = "top-secret-key"
        manager = ProviderManager(config={"provider":"ollama","ollama_model":"qwen3"},
            environ={"YUKI_OLLAMA_MODEL":"qwen3","OPENAI_API_KEY":secret})
        catalog = manager.catalog()
        self.assertTrue(any(item["name"] == "ollama" and item["selected"] for item in catalog))
        self.assertNotIn(secret, repr(catalog))

    def test_task_routing_prefers_local_and_requires_live_adapter_availability(self):
        from unittest.mock import patch

        class AvailableAdapter:
            def is_available(self):
                return True
            def generate(self, prompt):
                return "ok"

        manager = ProviderManager(
            config={"provider": "ollama", "ollama_model": "qwen3"},
            environ={"YUKI_OLLAMA_MODEL": "qwen3"},
        )
        with patch.object(manager, "_build_adapter", return_value=AvailableAdapter()):
            result = manager.route_for_task(
                required_capabilities=("text", "local_inference"),
                preference=("ollama",),
            )
        self.assertEqual(result, "Selected provider for task: ollama")
        self.assertEqual(manager.current().name, "ollama")

    def test_task_resolution_does_not_mutate_shared_provider_selection(self):
        from unittest.mock import patch

        class AvailableAdapter:
            def is_available(self):
                return True
            def generate(self, prompt):
                return "task-local result"

        manager = ProviderManager(
            config={"provider": "ollama", "ollama_model": "qwen3"},
            environ={"YUKI_OLLAMA_MODEL": "qwen3"},
        )
        existing_adapter = object()
        manager._selected_adapter = existing_adapter
        with patch.object(manager, "_build_adapter", return_value=AvailableAdapter()):
            provider, adapter = manager.resolve_for_task(
                required_capabilities=("text", "local_inference"),
                preference=("ollama",),
            )
        self.assertEqual(provider, "ollama")
        self.assertNotIsInstance(adapter, type(existing_adapter))
        self.assertEqual(manager.current().name, "ollama")
        self.assertIs(manager._selected_adapter, existing_adapter)

    def test_task_resolution_failure_preserves_shared_selection(self):
        from unittest.mock import patch

        class UnavailableAdapter:
            def is_available(self):
                return False

        manager = ProviderManager(
            config={"provider": "ollama", "ollama_model": "qwen3"},
            environ={"YUKI_OLLAMA_MODEL": "qwen3"},
        )
        existing_adapter = object()
        manager._selected_adapter = existing_adapter
        with patch.object(manager, "_build_adapter", return_value=UnavailableAdapter()):
            with self.assertRaisesRegex(RuntimeError, "no provider switch was made"):
                manager.resolve_for_task(required_capabilities=("text",), preference=("ollama",))
        self.assertEqual(manager.current().name, "ollama")
        self.assertIs(manager._selected_adapter, existing_adapter)

    def test_task_routing_never_switches_to_unavailable_candidate(self):
        from unittest.mock import patch

        class UnavailableAdapter:
            def is_available(self):
                return False
            def generate(self, prompt):
                return "must not run"

        manager = ProviderManager(
            config={"provider": "ollama", "ollama_model": "qwen3"},
            environ={"YUKI_OLLAMA_MODEL": "qwen3"},
        )
        with patch.object(manager, "_build_adapter", return_value=UnavailableAdapter()):
            with self.assertRaisesRegex(RuntimeError, "no provider switch was made"):
                manager.route_for_task(required_capabilities=("text",), preference=("ollama",))
        self.assertEqual(manager.current().name, "ollama")
        self.assertIsNone(manager._selected_adapter)

    def test_task_routing_excludes_cloud_until_explicitly_allowed(self):
        manager = ProviderManager(
            config={"provider": "ollama", "ollama_model": "qwen3"},
            environ={
                "YUKI_OLLAMA_MODEL": "qwen3",
                "YUKI_ANTHROPIC_MODEL": "claude-test",
                "ANTHROPIC_API_KEY": "test-key",
            },
        )
        # With local configured but unready remote providers excluded by policy,
        # the router must not select a cloud model to satisfy the task implicitly.
        selected = manager.routing_registry().route(required=("text",), preference=("ollama",))
        self.assertEqual(selected.provider, "ollama")
        cloud = manager.routing_registry().route(
            required=("native_messages_api",), preference=("anthropic",), allow_cloud=True
        )
        self.assertEqual(cloud.provider, "anthropic")

    def test_additional_provider_selection_uses_real_factory(self):
        cases = (
            ("deepseek", {"YUKI_DEEPSEEK_MODEL": "deepseek-chat", "DEEPSEEK_API_KEY": "test"}),
            ("kimi", {"YUKI_KIMI_MODEL": "kimi-k2", "MOONSHOT_API_KEY": "test"}),
            ("minimax", {"YUKI_MINIMAX_MODEL": "MiniMax-M2", "MINIMAX_API_KEY": "test"}),
            ("openrouter", {"YUKI_OPENROUTER_MODEL": "openai/gpt-4.1-mini", "OPENROUTER_API_KEY": "test"}),
            ("tokenrouter", {"YUKI_TOKENROUTER_MODEL": "vendor/model", "TOKENROUTER_API_KEY": "test"}),
        )
        for name, environ in cases:
            with self.subTest(provider=name):
                manager = ProviderManager(environ=environ)
                result = manager.select(name)
                self.assertEqual(result, f"Selected provider: {name}")
                adapter = manager.create_selected_adapter()
                self.assertEqual(adapter.model, environ[next(k for k in environ if k.startswith("YUKI_"))])
                self.assertTrue(adapter.is_available())
                self.assertEqual(manager.current().name, name)

if __name__ == "__main__":
    unittest.main()
