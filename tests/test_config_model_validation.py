"""Regression tests for strict Ollama model identifiers in runtime config."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from core.config import load_config


class OllamaModelConfigValidationTests(unittest.TestCase):
    def _config_path(self, directory: str, model_marker: object = ...) -> Path:
        path = Path(directory) / "config.yaml"
        data = {}
        if model_marker is not ...:
            data["ollama_model"] = model_marker
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
        return path

    def test_default_and_valid_yaml_model_are_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._config_path(directory)
            with patch.dict("os.environ", {}, clear=True):
                self.assertEqual(load_config(path)["ollama_model"], "llama3.2:3b")

            path = self._config_path(directory, "qwen2.5:7b")
            with patch.dict("os.environ", {}, clear=True):
                self.assertEqual(load_config(path)["ollama_model"], "qwen2.5:7b")

    def test_invalid_yaml_model_values_are_rejected(self):
        invalid_models = ("", "   ", "model with spaces", "bad\nmodel", "model\tname", "x" * 121, None, 7, True)
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {}, clear=True):
            path = Path(directory) / "config.yaml"
            for model in invalid_models:
                with self.subTest(model=repr(model)):
                    path.write_text(yaml.safe_dump({"ollama_model": model}), encoding="utf-8")
                    with self.assertRaisesRegex(RuntimeError, "ollama_model"):
                        load_config(path)

    def test_invalid_environment_model_overrides_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._config_path(directory)
            invalid_models = ("", "   ", "model with spaces", "bad\nmodel", "model\tname", "x" * 121)
            for model in invalid_models:
                with self.subTest(model=repr(model)), patch.dict("os.environ", {"YUKI_OLLAMA_MODEL": model}, clear=True):
                    with self.assertRaisesRegex(RuntimeError, "ollama_model"):
                        load_config(path)

    def test_valid_environment_model_override_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._config_path(directory, "llama3.2:3b")
            with patch.dict("os.environ", {"YUKI_OLLAMA_MODEL": "qwen2.5:7b"}, clear=True):
                self.assertEqual(load_config(path)["ollama_model"], "qwen2.5:7b")


if __name__ == "__main__":
    unittest.main()
