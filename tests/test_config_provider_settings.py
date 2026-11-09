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

    def test_missing_model_uses_documented_default(self):
        config = self._load("provider: openai\nworkspace: $WORKSPACE\n")
        self.assertEqual(config["openai_model"], "gpt-4.1-mini")

    def test_invalid_openai_model_fails_configuration(self):
        for value in ("", " " + "x" * 121, "bad\nmodel"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(RuntimeError, "openai_model"):
                    self._load(
                        "provider: openai\nopenai_model: " + repr(value) + "\nworkspace: $WORKSPACE\n"
                    )


if __name__ == "__main__":
    unittest.main()
