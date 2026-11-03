import unittest
from unittest.mock import patch

from providers.model_manager import (
    HardwareProfile, LocalModelManager, OllamaLocalClient, recommend_models,
)


class FakeOllama:
    def status(self):
        return {"available": True, "models": [{"name": "qwen3:4b"}]}


class LocalModelManagerTests(unittest.TestCase):
    def test_low_memory_recommends_small_models(self):
        profile = HardwareProfile(3 * 1024**3, 2 * 1024**3, None, 4)
        names = [item.model for item in recommend_models(profile)]
        self.assertEqual(names, ["qwen3:0.6b", "gemma3:1b"])

    def test_medium_memory_recommends_medium_models(self):
        profile = HardwareProfile(12 * 1024**3, 7 * 1024**3, None, 8)
        names = [item.model for item in recommend_models(profile)]
        self.assertIn("qwen3:1.7b", names)

    def test_vram_can_inform_recommendation(self):
        profile = HardwareProfile(8 * 1024**3, 2 * 1024**3, None, 8,
                                  "Test GPU", 12 * 1024**3)
        self.assertIn("qwen3:8b", [x.model for x in recommend_models(profile)])

    def test_unknown_memory_falls_back_conservatively(self):
        profile = HardwareProfile(None, None, None, 2)
        self.assertEqual(recommend_models(profile)[0].model, "qwen3:0.6b")
        self.assertEqual(recommend_models(profile)[0].confidence, "low")

    def test_rejects_non_loopback_ollama_endpoint(self):
        with self.assertRaises(ValueError):
            OllamaLocalClient("https://example.com")

    def test_manager_does_not_download(self):
        manager = LocalModelManager(ollama_client=FakeOllama())
        result = manager.inspect()
        self.assertTrue(result["ollama"]["available"])
        self.assertFalse(result["downloads_performed"])
        self.assertEqual(result["installation_policy"], "explicit_user_approval_required")

    def test_unavailable_ollama_is_reported(self):
        class Unavailable:
            def status(self):
                return {"available": False, "models": []}
        result = LocalModelManager(ollama_client=Unavailable()).inspect()
        self.assertFalse(result["ollama"]["available"])


if __name__ == "__main__":
    unittest.main()
