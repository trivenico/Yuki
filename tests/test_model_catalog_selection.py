"""Focused tests for shared model catalog and safe selection validation."""
import json
import unittest
from unittest.mock import patch

from core.model_catalog import get_provider, list_providers
from core.model_selection import list_installed_ollama_models, model_selection_options, validate_model_selection


class _Response:
    def __init__(self, payload):
        self.payload = payload
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, limit):
        return self.payload[:limit]


class ModelCatalogTests(unittest.TestCase):
    def test_catalog_returns_copy_safe_local_and_cloud_entries(self):
        local = list_providers("local")
        cloud = list_providers("cloud")
        self.assertTrue(local)
        self.assertTrue(cloud)
        self.assertTrue(all(item["kind"] == "local" for item in local))
        self.assertTrue(all(item["kind"] == "cloud" for item in cloud))
        local[0]["models"].append({"id": "mutated"})
        self.assertNotIn("mutated", str(list_providers("local")))

    def test_catalog_exposes_gemini_nvidia_and_custom_model_ids(self):
        cloud_ids = {item["id"] for item in list_providers("cloud")}
        self.assertIn("gemini_api", cloud_ids)
        self.assertIn("nvidia", cloud_ids)
        self.assertTrue(all(item["custom_model_allowed"] for item in list_providers()))

    def test_model_options_default_to_local(self):
        self.assertEqual(model_selection_options()["default_kind"], "local")
        self.assertTrue(model_selection_options("cloud")["providers"])

    def test_selection_rejects_unknown_fields_that_could_leak_secrets(self):
        with self.assertRaisesRegex(ValueError, "unsupported fields"):
            validate_model_selection({
                "provider": "openai",
                "model": "gpt-4.1-mini",
                "api_key": "must-not-be-accepted",
            })

    def test_cloud_selection_requires_https(self):
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            validate_model_selection({
                "provider": "openai",
                "model": "gpt-4.1-mini",
                "base_url": "http://api.example.invalid/v1",
            })

    def test_ollama_discovery_is_loopback_only(self):
        with patch("core.model_selection.urllib.request.urlopen") as open_url:
            with self.assertRaisesRegex(ValueError, "loopback"):
                list_installed_ollama_models("http://192.0.2.10:11434")
        open_url.assert_not_called()

    def test_ollama_discovery_bounds_and_deduplicates_models(self):
        payload = json.dumps({"models": [{"name": "llama3.2:3b"}, {"name": "llama3.2:3b"}, {"name": ""}]}).encode()
        with patch("core.model_selection.urllib.request.urlopen", return_value=_Response(payload)):
            models = list_installed_ollama_models()
        self.assertEqual([item["id"] for item in models], ["llama3.2:3b"])


if __name__ == "__main__":
    unittest.main()
