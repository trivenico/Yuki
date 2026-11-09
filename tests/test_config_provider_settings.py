"""Regression tests for provider model settings loaded from YAML and environment."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core.config import load_config
from providers.factory import create_provider
from providers.openai_compatible import OpenAICompatibleProvider


class OpenAIModelConfigurationTests(unittest.TestCase):
    def _load(self, yaml_text, env=None):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.yaml"
            workspace = Path(directory) / "workspace"
            config_path.write_text(yaml_text.replace("$WORKSPACE", str(workspace)), encoding="utf-8")
            with patch.dict(os.environ, env or {}, clear=True):
                return load_config(config_path)

    def test_yaml_openai_model_is_returned_to_provider_factory(self):
        config = self._load(
            "provider: openai\nopenai_model: yuki-configured-model\nworkspace: $WORKSPACE\n"
        )
        self.assertEqual(config["openai_model"], "yuki-configured-model")
        provider = create_provider(config, environ={})
        self.assertIsInstance(provider, OpenAICompatibleProvider)
        self.assertEqual(provider.model, "yuki-configured-model")

    def test_environment_model_override_takes_precedence(self):
        config = self._load(
            "provider: openai\nopenai_model: yaml-model\nworkspace: $WORKSPACE\n",
            {"YUKI_OPENAI_MODEL": "environment-model"},
        )
        self.assertEqual(config["openai_model"], "environment-model")
        provider = create_provider(config, environ={"YUKI_OPENAI_MODEL": "environment-model"})
        self.assertEqual(provider.model, "environment-model")

    def test_provider_defaults_to_real_openai_adapter(self):
        config = self._load("workspace: $WORKSPACE\n")
        self.assertEqual(config["provider"], "openai")

    def test_missing_model_uses_documented_default(self):
        config = self._load("provider: openai\nworkspace: $WORKSPACE\n")
        self.assertEqual(config["openai_model"], "gpt-4.1-mini")

    def test_ollama_memory_settings_are_loaded_and_passed_to_provider(self):
        config = self._load(
            "provider: ollama\nollama_model: tiny-model\n"
            "ollama_num_ctx: 1024\nollama_num_predict: 128\n"
            "ollama_num_batch: 64\nollama_num_thread: 2\n"
            "workspace: $WORKSPACE\n"
        )
        self.assertEqual(
            (config["ollama_num_ctx"], config["ollama_num_predict"],
             config["ollama_num_batch"], config["ollama_num_thread"]),
            (1024, 128, 64, 2),
        )
        provider = create_provider(config, environ={})
        self.assertEqual(provider.options, {
            "num_ctx": 1024, "num_predict": 128,
            "num_batch": 64, "num_thread": 2,
        })

    def test_ollama_memory_settings_have_safe_defaults(self):
        config = self._load(
            "provider: ollama\nollama_model: tiny-model\nworkspace: $WORKSPACE\n"
        )
        self.assertEqual(
            (config["ollama_num_ctx"], config["ollama_num_predict"],
             config["ollama_num_batch"], config["ollama_num_thread"]),
            (2048, 512, 128, 0),
        )

    def test_invalid_ollama_memory_settings_are_rejected(self):
        for key, value in (
            ("ollama_num_ctx", 255), ("ollama_num_ctx", 32769),
            ("ollama_num_predict", 0), ("ollama_num_batch", 15),
            ("ollama_num_thread", -1),
        ):
            with self.subTest(key=key, value=value):
                with self.assertRaises(RuntimeError):
                    self._load(
                        f"provider: ollama\nollama_model: tiny-model\n{key}: {value}\n"
                        "workspace: $WORKSPACE\n"
                    )

    def test_invalid_ollama_memory_environment_override_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "YUKI_OLLAMA_NUM_CTX"):
            self._load(
                "provider: ollama\nollama_model: tiny-model\nworkspace: $WORKSPACE\n",
                {"YUKI_OLLAMA_NUM_CTX": "not-an-integer"},
            )

    def test_invalid_openai_model_fails_configuration(self):
        for value in ("", " " + "x" * 121):
            with self.subTest(value=value):
                with self.assertRaisesRegex(RuntimeError, "openai_model"):
                    self._load(
                        "provider: openai\nopenai_model: " + repr(value) + "\nworkspace: $WORKSPACE\n"
                    )


if __name__ == "__main__":
    unittest.main()
