import os
import tempfile
import unittest
from pathlib import Path

import yaml

from core.provider_setup import run_provider_setup, save_provider_preferences


class ProviderSetupTests(unittest.TestCase):
    def test_saves_non_secret_preferences_and_preserves_other_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text("workspace: ./data\nvoice_enabled: false\n", encoding="utf-8")
            saved = save_provider_preferences(path, "openai", "gpt-4.1-mini")
            config = yaml.safe_load(saved.read_text(encoding="utf-8"))
            self.assertEqual(config["provider"], "openai")
            self.assertEqual(config["openai_model"], "gpt-4.1-mini")
            self.assertEqual(config["workspace"], "./data")
            self.assertNotIn("OPENAI_API_KEY", saved.read_text(encoding="utf-8"))
            if os.name == "posix":
                self.assertEqual(saved.stat().st_mode & 0o777, 0o600)
                self.assertEqual(saved.parent.stat().st_mode & 0o777, 0o700)

    def test_rejects_source_checkout_configuration(self):
        with self.assertRaisesRegex(RuntimeError, "source checkout"):
            save_provider_preferences(Path(__file__).resolve().parents[1] / "config.yaml",
                                      "openai", "gpt-4.1-mini")

    def test_invalid_provider_does_not_create_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            with self.assertRaises(ValueError):
                save_provider_preferences(path, "unknown", "model")
            self.assertFalse(path.exists())

    def test_setup_prompts_for_provider_and_model_without_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            answers = iter(["ollama", "qwen2.5:7b"])
            output = []
            result = run_provider_setup(input_fn=lambda prompt: next(answers),
                                        output_fn=output.append, path=path)
            self.assertEqual(result, 0)
            self.assertEqual(yaml.safe_load(path.read_text())["ollama_model"], "qwen2.5:7b")
            self.assertFalse(any("key" in line.lower() and "secret" in line.lower()
                                 for line in output))

    def test_cancelled_setup_leaves_config_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text("provider: openai\n", encoding="utf-8")
            before = path.read_bytes()
            output = []
            result = run_provider_setup(input_fn=lambda prompt: (_ for _ in ()).throw(KeyboardInterrupt()),
                                        output_fn=output.append, path=path)
            self.assertEqual(result, 2)
            self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
