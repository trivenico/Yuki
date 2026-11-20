"""Regression tests for the installed runtime's provider configuration."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.config import load_config


class ProviderConfigLoadingTests(unittest.TestCase):
    def _load(self, yaml_text="", environ=None):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text(yaml_text, encoding="utf-8")
            with patch.dict(os.environ, environ or {}, clear=True):
                return load_config(path)

    def test_default_configuration_selects_openai_and_exposes_timeout(self):
        config = self._load("workspace: ./workspace\n")
        self.assertEqual(config["provider"], "openai")
        self.assertEqual(config["openai_model"], "gpt-4.1-mini")
        self.assertEqual(config["provider_timeout_seconds"], 45)

    def test_yaml_provider_and_timeout_are_loaded(self):
        config = self._load(
            "provider: ollama\nprovider_timeout_seconds: 22\nollama_model: local-test\n"
        )
        self.assertEqual(config["provider"], "ollama")
        self.assertEqual(config["provider_timeout_seconds"], 22)

    def test_environment_overrides_provider_model_and_timeout(self):
        config = self._load(
            "provider: ollama\n",
            {"YUKI_PROVIDER": "openai", "YUKI_OPENAI_MODEL": "env-model",
             "YUKI_PROVIDER_TIMEOUT_SECONDS": "12.5"},
        )
        self.assertEqual(config["provider"], "openai")
        self.assertEqual(config["openai_model"], "env-model")
        self.assertEqual(config["provider_timeout_seconds"], 12.5)

    def test_unknown_provider_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "provider must be one of"):
            self._load("provider: automatic-fallback\n")

    def test_invalid_timeout_is_rejected(self):
        for value in ("nope", "0", "121", "true"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(RuntimeError, "provider_timeout_seconds"):
                    self._load("provider_timeout_seconds: " + value + "\n")

    def test_empty_model_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "openai_model"):
            self._load("openai_model: ''\n")


if __name__ == "__main__":
    unittest.main()
