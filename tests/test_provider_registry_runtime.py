import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch
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
        self.assertTrue(entries["llama_cpp"]["implemented"])

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

    def test_selected_llama_cpp_provider_exposes_bounded_agent_model(self):
        from providers.agent_adapter import ProviderAgentAdapter
        class FakeLlama:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
            def create_chat_completion(self, **kwargs):
                return {"choices": [{"message": {"content": "local"}, "finish_reason": "stop"}]}
        native = types.ModuleType("llama_cpp")
        native.Llama = FakeLlama
        with tempfile.TemporaryDirectory() as directory:
            model_path = Path(directory) / "model.gguf"
            model_path.write_bytes(b"fixture")
            manager = ProviderManager(
                {"provider": "llama_cpp", "llama_cpp_model_path": str(model_path)},
                environ={},
            )
            with patch.dict(sys.modules, {"llama_cpp": native}):
                self.assertEqual(manager.select("llama_cpp"), "Selected provider: llama_cpp")
                model = manager.create_selected_agent_model()
                self.assertIsInstance(model, ProviderAgentAdapter)
                self.assertEqual(model(
                    messages=[{"role": "user", "content": "hello"}],
                    tools=[], tool_choice="none",
                )["text"], "local")

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

    def test_custom_factory_must_return_provider_contract(self):
        manager = ProviderManager(environ={})
        manager.register(type(manager.current())("custom", (), True), factory=lambda: object())
        with self.assertRaisesRegex(TypeError, "generate"):
            manager._build_adapter("custom")

if __name__ == "__main__":
    unittest.main()
