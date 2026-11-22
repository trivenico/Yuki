import os
import tempfile
import unittest
from pathlib import Path

import yaml

from core.provider_setup import _contains_secret_field, run_provider_setup, save_provider_preferences


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

    @unittest.skipUnless(os.name == "posix", "POSIX permission semantics")
    def test_preserves_existing_parent_directory_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            parent.chmod(0o755)
            path = parent / "config.yaml"
            save_provider_preferences(path, "openai", "gpt-4.1-mini")
            self.assertEqual(parent.stat().st_mode & 0o777, 0o755)

    def test_rejects_source_checkout_configuration(self):
        with self.assertRaisesRegex(RuntimeError, "source checkout"):
            save_provider_preferences(Path(__file__).resolve().parents[1] / "config.yaml",
                                      "openai", "gpt-4.1-mini")

    def test_refuses_to_rewrite_existing_yaml_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            original = "provider: openai\nopenai_api_key: do-not-touch\n"
            path.write_text(original, encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "credential-like fields"):
                save_provider_preferences(path, "ollama", "local-model")
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_secret_detection_handles_recursive_yaml_aliases(self):
        recursive = yaml.safe_load("items: &items [*items]")
        self.assertFalse(_contains_secret_field(recursive))

        recursive_with_secret = yaml.safe_load(
            "items: &items [*items, {nested: {api_key: hidden}}]"
        )
        self.assertTrue(_contains_secret_field(recursive_with_secret))

    def test_invalid_provider_does_not_create_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            with self.assertRaises(ValueError):
                save_provider_preferences(path, "unknown", "model")
            self.assertFalse(path.exists())

    def test_saves_local_gguf_path_without_requesting_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            model_path = Path(directory) / "yuki.gguf"
            model_path.write_bytes(b"fixture")
            config_path = Path(directory) / "config.yaml"
            save_provider_preferences(config_path, "llama_cpp", str(model_path))
            config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            self.assertEqual(config["provider"], "llama_cpp")
            self.assertEqual(config["llama_cpp_model_path"], str(model_path.resolve()))
            self.assertNotIn("api_key", config)

    def test_setup_prompts_for_local_gguf_path(self):
        with tempfile.TemporaryDirectory() as directory:
            model_path = Path(directory) / "local.gguf"
            model_path.write_bytes(b"fixture")
            config_path = Path(directory) / "config.yaml"
            answers = iter(["llama_cpp", str(model_path)])
            output = []
            result = run_provider_setup(
                input_fn=lambda prompt: next(answers), output_fn=output.append,
                path=config_path,
            )
            self.assertEqual(result, 0)
            config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            self.assertEqual(config["provider"], "llama_cpp")
            self.assertEqual(config["llama_cpp_model_path"], str(model_path.resolve()))
            self.assertTrue(any("local-inference" in line for line in output))

    def test_local_setup_rejects_missing_or_non_gguf_model(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.yaml"
            with self.assertRaisesRegex(ValueError, "existing regular local GGUF"):
                save_provider_preferences(config_path, "llama_cpp", str(Path(directory) / "missing.gguf"))
            binary = Path(directory) / "model.bin"
            binary.write_bytes(b"fixture")
            with self.assertRaisesRegex(ValueError, "existing regular local GGUF"):
                save_provider_preferences(config_path, "llama_cpp", str(binary))
            self.assertFalse(config_path.exists())

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
