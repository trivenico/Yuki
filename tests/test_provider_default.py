"""Regression tests for safe provider selection defaults."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.config import load_config


class ProviderDefaultTests(unittest.TestCase):
    def test_minimal_config_defaults_to_real_provider_not_mock(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.yaml"
            config_path.write_text("workspace: ./workspace\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                config = load_config(config_path)
        self.assertEqual(config["provider"], "openai")
        self.assertNotEqual(config["provider"], "mock")

    def test_explicit_config_provider_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.yaml"
            config_path.write_text("provider: ollama\nworkspace: ./workspace\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                config = load_config(config_path)
        self.assertEqual(config["provider"], "ollama")

    def test_environment_provider_override_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.yaml"
            config_path.write_text("provider: openai\nworkspace: ./workspace\n", encoding="utf-8")
            with patch.dict(os.environ, {"YUKI_PROVIDER": "ollama"}, clear=True):
                config = load_config(config_path)
        self.assertEqual(config["provider"], "ollama")


if __name__ == "__main__":
    unittest.main()
