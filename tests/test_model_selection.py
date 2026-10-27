import json
import unittest
from unittest.mock import patch
from urllib.error import URLError

from core.model_catalog import get_provider, list_providers
from core.model_selection import list_installed_ollama_models, validate_model_selection
from providers.factory import create_provider
from providers.gemini_api import GeminiAPIProvider
from providers.nvidia_nim import NvidiaNIMProvider

class FakeResponse:
    def __init__(self, payload): self.payload = payload
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self, limit): return self.payload[:limit]

class ModelSelectionTests(unittest.TestCase):
    def test_catalog_exposes_local_and_cloud_groups_and_custom_model_support(self):
        local = list_providers("local")
        cloud = list_providers("cloud")
        self.assertTrue(local)
        self.assertTrue(cloud)
        self.assertTrue(all(item["kind"] == "local" for item in local))
        self.assertTrue(all(item["kind"] == "cloud" for item in cloud))
        self.assertTrue(all(item["custom_model_allowed"] for item in local + cloud))
        self.assertIn("gemini_api", {item["id"] for item in cloud})
        self.assertIn("nvidia", {item["id"] for item in cloud})

    def test_ollama_models_are_discovered_from_loopback_tags_endpoint(self):
        payload = json.dumps({"models": [{"name": "qwen2.5:7b"}, {"name": "llama3.2:3b"}]}).encode()
        with patch("core.model_selection.urllib.request.urlopen", return_value=FakeResponse(payload)) as open_url:
            result = list_installed_ollama_models()
        self.assertEqual([item["id"] for item in result], ["qwen2.5:7b", "llama3.2:3b"])
        self.assertIn("/api/tags", open_url.call_args.args[0].full_url)

    def test_ollama_discovery_refuses_non_loopback_http_before_network(self):
        with patch("core.model_selection.urllib.request.urlopen") as open_url:
            with self.assertRaisesRegex(ValueError, "loopback"):
                list_installed_ollama_models("http://192.0.2.10:11434")
        open_url.assert_not_called()

    def test_cloud_model_selection_requires_https_and_rejects_secret_fields(self):
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            validate_model_selection({"provider": "openai", "model": "gpt-4.1-mini", "base_url": "http://api.example.com/v1"})
        with self.assertRaisesRegex(ValueError, "unsupported fields"):
            validate_model_selection({"provider": "openai", "model": "gpt-4.1-mini", "api_key": "do-not-accept"})

    def test_custom_model_id_is_preserved(self):
        selected = validate_model_selection({"provider": "gemini_api", "model": "my-enabled-model"})
        self.assertEqual(selected["model"], "my-enabled-model")
        self.assertEqual(selected["api_key_env"], "GEMINI_API_KEY")

    def test_factory_registers_gemini_and_nvidia_models(self):
        gemini = create_provider({"provider": "gemini_api", "model": "gemini-2.5-flash"}, environ={})
        nvidia = create_provider({"provider": "nvidia", "model": "openai/gpt-oss-20b"}, environ={})
        self.assertIsInstance(gemini, GeminiAPIProvider)
        self.assertIsInstance(nvidia, NvidiaNIMProvider)
        self.assertFalse(gemini.is_available())
        self.assertFalse(nvidia.is_available())

if __name__ == "__main__":
    unittest.main()