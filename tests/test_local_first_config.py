"""Configuration regression tests for safe local-first provider defaults."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.config import load_config


class LocalFirstConfigTests(unittest.TestCase):
    def test_minimal_config_defaults_to_ollama(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.yaml"
            path.write_text("workspace: ./workspace\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                config = load_config(path)
        self.assertEqual(config["provider"], "ollama")

    def test_explicit_provider_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.yaml"
            path.write_text("provider: openai\nworkspace: ./workspace\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                config = load_config(path)
        self.assertEqual(config["provider"], "openai")

    def test_environment_override_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.yaml"
            path.write_text("provider: openai\nworkspace: ./workspace\n", encoding="utf-8")
            with patch.dict(os.environ, {"YUKI_PROVIDER": "ollama"}, clear=True):
                config = load_config(path)
        self.assertEqual(config["provider"], "ollama")


if __name__ == "__main__":
    unittest.main()
