import unittest
from unittest.mock import patch

from providers.ollama import OllamaProvider
from scripts import smoke_test_ollama


class OllamaSmokeTestTests(unittest.TestCase):
    def test_rejects_non_ollama_configuration(self):
        with patch.object(smoke_test_ollama, "load_config", return_value={"provider": "mock"}), \
             patch("builtins.print"):
            self.assertEqual(smoke_test_ollama.main(), 2)

    def test_unavailable_model_fails_without_inference(self):
        provider = OllamaProvider(model="qwen2.5:3b")
        with patch.object(smoke_test_ollama, "load_config", return_value={
                 "provider": "ollama", "ollama_model": "qwen2.5:3b"}), \
             patch.object(smoke_test_ollama, "create_provider", return_value=provider), \
             patch.object(OllamaProvider, "is_available", return_value=False), \
             patch.object(smoke_test_ollama.YukiAgent, "ask") as ask, \
             patch("builtins.print"):
            self.assertEqual(smoke_test_ollama.main(), 3)
            ask.assert_not_called()

    def test_success_requires_real_agent_response_marker(self):
        provider = OllamaProvider(model="qwen2.5:3b")
        with patch.object(smoke_test_ollama, "load_config", return_value={
                 "provider": "ollama", "ollama_model": "qwen2.5:3b"}), \
             patch.object(smoke_test_ollama, "create_provider", return_value=provider), \
             patch.object(OllamaProvider, "is_available", return_value=True), \
             patch.object(smoke_test_ollama.YukiAgent, "ask", return_value="YUKI_OLLAMA_SMOKE_OK"), \
             patch("builtins.print"):
            self.assertEqual(smoke_test_ollama.main(), 0)


if __name__ == "__main__":
    unittest.main()
