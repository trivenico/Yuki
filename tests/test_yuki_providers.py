import unittest
from yuki_providers import ProviderManager
from providers import MockProvider, OllamaProvider, HybridProvider

class ProviderManagerAlignmentTests(unittest.TestCase):
    def test_registry_matches_supported_factory_providers(self):
        manager=ProviderManager(environ={})
        entries={item["name"]:item for item in manager.list_providers()}
        self.assertTrue(entries["mock"]["implemented"])
        self.assertTrue(entries["ollama"]["implemented"])
        self.assertTrue(entries["hybrid"]["implemented"])
        self.assertFalse(entries["openai"]["implemented"])
        self.assertFalse(entries["anthropic"]["implemented"])
        self.assertFalse(entries["gemini"]["implemented"])
    def test_mock_can_be_created(self):
        self.assertIsInstance(ProviderManager(environ={}).create_selected_adapter(),MockProvider)
    def test_ollama_selection_requires_valid_model_config_and_preserves_selection(self):
        manager=ProviderManager(environ={})
        self.assertIn("Cannot select ollama",manager.select("ollama"))
        self.assertEqual(manager.current().name,"mock")
    def test_ollama_and_hybrid_create_structural_provider_adapters(self):
        manager=ProviderManager({"ollama_model":"test-model"},environ={})
        self.assertIn("Selected provider: ollama",manager.select("ollama"))
        self.assertIsInstance(manager.create_selected_adapter(),OllamaProvider)
        self.assertIn("Selected provider: hybrid",manager.select("hybrid"))
        self.assertIsInstance(manager.create_selected_adapter(),HybridProvider)
    def test_unimplemented_provider_does_not_change_selection(self):
        manager=ProviderManager(environ={})
        self.assertIn("not implemented",manager.select("openai"))
        self.assertEqual(manager.current().name,"mock")
    def test_bad_factory_output_is_rejected(self):
        manager=ProviderManager(environ={})
        manager.register(type(manager.current())("broken",(),True),factory=lambda: object())
        with self.assertRaisesRegex(TypeError,"generate\\(\\)"):
            manager._build_adapter("broken")

if __name__=="__main__": unittest.main()
