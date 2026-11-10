import unittest
from unittest.mock import patch

from providers.model_registry import ModelCapabilityRegistry
from yuki_providers import ProviderManager


class _AvailableProvider:
    def generate(self, prompt):
        return "ok"

    def is_available(self):
        return True


class RuntimeModelCapabilityRoutingTests(unittest.TestCase):
    def test_local_ollama_model_is_routed_and_pinned(self):
        manager = ProviderManager(
            config={"ollama_model": "qwen3"},
            environ={},
        )
        profiles = manager.model_capability_registry()
        selected = profiles.route(required=("text",))
        self.assertEqual(selected.model_id, "ollama/qwen3")
        self.assertEqual(selected.privacy, "local_runtime")
        with patch("providers.factory.create_provider", return_value=_AvailableProvider()) as factory:
            adapter = manager.create_routed_model_adapter(required=("text",))
        self.assertIsInstance(adapter, _AvailableProvider)
        config = factory.call_args.args[0]
        self.assertEqual(config["provider"], "ollama")
        self.assertEqual(config["ollama_model"], "qwen3")

    def test_cloud_model_requires_explicit_consent_and_exact_pin(self):
        manager = ProviderManager(
            config={},
            environ={"OPENAI_API_KEY": "test-secret", "YUKI_OPENAI_MODEL": "gpt-4.1-mini"},
        )
        registry = manager.model_capability_registry()
        with self.assertRaises(LookupError):
            registry.route(required=("text",))
        with patch("providers.factory.create_provider", return_value=_AvailableProvider()) as factory:
            adapter = manager.create_routed_model_adapter(
                required=("text",), allow_cloud=True,
                provider_preference=("openai",),
                model_preference=("openai/gpt-4.1-mini",),
            )
        self.assertIsInstance(adapter, _AvailableProvider)
        config, kwargs = factory.call_args.args[0], factory.call_args.kwargs
        self.assertEqual(config["provider"], "openai")
        self.assertEqual(kwargs["environ"]["YUKI_OPENAI_MODEL"], "gpt-4.1-mini")
        self.assertNotIn("test-secret", repr(manager.catalog()))

    def test_unknown_model_capabilities_are_not_inferred(self):
        manager = ProviderManager(
            config={"ollama_model": "qwen3"},
            environ={},
        )
        with self.assertRaises(LookupError):
            manager.create_routed_model_adapter(required=("vision_input",))

    def test_unknown_remote_ollama_endpoint_is_not_routed_as_local(self):
        manager = ProviderManager(
            config={"ollama_model": "qwen3", "ollama_base_url": "https://remote.example"},
            environ={},
        )
        registry = manager.model_capability_registry()
        with self.assertRaises(LookupError):
            registry.route(required=("text",))

    def test_unavailable_exact_model_does_not_fallback(self):
        manager = ProviderManager(
            config={"ollama_model": "qwen3"},
            environ={},
        )
        class Unavailable(_AvailableProvider):
            def is_available(self):
                return False
        with patch("providers.factory.create_provider", return_value=Unavailable()):
            with self.assertRaisesRegex(RuntimeError, "no fallback"):
                manager.create_routed_model_adapter(required=("text",))


if __name__ == "__main__":
    unittest.main()
