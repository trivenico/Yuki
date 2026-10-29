"""Offline tests for the shared provider catalog and safe model selection."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from core.model_catalog import get_provider, list_providers
from core.model_selection import (
    _NoRedirectHandler, apply_model_selection, list_installed_ollama_models,
    model_selection_options, persist_model_selection, validate_model_selection,
)


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
    def test_catalog_filters_and_marks_runtime_support_truthfully(self):
        self.assertTrue(all(p["kind"] == "local" for p in list_providers("local")))
        entries = {p["id"]: p for p in list_providers()}
        self.assertTrue(entries["ollama"]["runtime_supported"])
        self.assertTrue(entries["openai"]["runtime_supported"])
        self.assertFalse(entries["gemini_api"]["runtime_supported"])
        self.assertFalse(entries["nvidia"]["runtime_supported"])
        self.assertNotIn("nvidia", {p["id"] for p in list_providers(supported_only=True)})

    def test_catalog_returns_copy_safe_models(self):
        local = list_providers("local")
        local[0]["models"].append({"id": "poison"})
        self.assertNotIn("poison", str(list_providers("local")))
        with self.assertRaises(ValueError):
            list_providers("unsupported")

    def test_options_default_to_local(self):
        self.assertEqual(model_selection_options()["default_kind"], "local")
        self.assertTrue(model_selection_options("cloud")["providers"])

    def test_selection_rejects_unknown_fields_and_secrets(self):
        with self.assertRaisesRegex(ValueError, "unsupported fields"):
            validate_model_selection({"provider": "openai", "model": "gpt-4.1-mini", "api_key": "secret"})

    def test_catalogued_but_unimplemented_provider_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "not supported"):
            validate_model_selection({"provider": "gemini_api", "model": "gemini-2.5-flash"})

    def test_model_id_must_be_nonempty_bounded_text(self):
        for value in ("", "  ", "x" * 121, "bad\nmodel"):
            with self.subTest(value=repr(value)), self.assertRaises(ValueError):
                validate_model_selection({"provider": "ollama", "model": value})

    def test_config_mapping_does_not_mutate_original(self):
        original = {"workspace": "./workspace", "provider": "ollama"}
        updated = apply_model_selection(original, {"provider": "openai", "model": "gpt-4.1-mini"})
        self.assertEqual(original, {"workspace": "./workspace", "provider": "ollama"})
        self.assertEqual(updated["provider"], "openai")
        self.assertEqual(updated["openai_model"], "gpt-4.1-mini")

    def test_provider_without_model_field_is_selectable_without_model(self):
        self.assertEqual(validate_model_selection({"provider": "gemini"}), {"provider": "gemini", "model": ""})
        with self.assertRaises(ValueError):
            validate_model_selection({"provider": "gemini", "model": "invented-model"})

    def test_persistence_preserves_config_and_never_writes_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            path.write_text("workspace: ./workspace\nvoice_enabled: false\n", encoding="utf-8")
            selected = persist_model_selection({"provider": "ollama", "model": "llama3.2:3b"}, path=path)
            saved = yaml.safe_load(path.read_text(encoding="utf-8"))
        self.assertEqual(selected["provider"], "ollama")
        self.assertEqual(saved["workspace"], "./workspace")
        self.assertFalse(saved["voice_enabled"])
        self.assertEqual(saved["ollama_model"], "llama3.2:3b")
        self.assertNotIn("api_key", saved)

    def test_unsupported_selection_does_not_create_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            with self.assertRaises(ValueError):
                persist_model_selection({"provider": "nvidia", "model": "example"}, path=path)
            self.assertFalse(path.exists())

    def test_discovery_only_accepts_loopback_http_and_disables_redirects(self):
        with patch("core.model_selection.urllib.request.build_opener") as opener:
            with self.assertRaisesRegex(ValueError, "loopback"):
                list_installed_ollama_models("http://192.0.2.10:11434")
        opener.assert_not_called()
        self.assertIsNone(_NoRedirectHandler().redirect_request(None, None, 302, "Found", {}, "https://evil.example/"))

    def test_discovery_bounds_and_deduplicates_models(self):
        payload = json.dumps({"models": [{"name": "llama3.2:3b"}, {"name": "llama3.2:3b"}, {"name": ""}]}).encode()
        with patch("core.model_selection.urllib.request.build_opener") as opener:
            opener.return_value.open.return_value = _Response(payload)
            models = list_installed_ollama_models()
        self.assertEqual([m["id"] for m in models], ["llama3.2:3b"])


if __name__ == "__main__":
    unittest.main()
