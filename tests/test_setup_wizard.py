import os
from pathlib import Path
import tempfile
import unittest

from core.setup_wizard import load_local_env, run_setup


class SetupWizardTests(unittest.TestCase):
    def test_ollama_setup_persists_model_without_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.yaml"
            config.write_text("name: Yuki\nworkspace: ./workspace\nprovider: mock\n", encoding="utf-8")
            answers = iter(["2", "qwen2.5:7b"])
            messages = []
            run_setup(config_file=config, env_file=root / ".env",
                      input_fn=lambda prompt: next(answers), secret_fn=lambda prompt: "",
                      output_fn=messages.append)
            saved = config.read_text(encoding="utf-8")
            self.assertIn("provider: ollama", saved)
            self.assertIn("ollama_model: qwen2.5:7b", saved)
            self.assertFalse((root / ".env").exists())

    def test_hybrid_setup_keeps_api_key_out_of_yaml_and_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.yaml"
            config.write_text("name: Yuki\nworkspace: ./workspace\nprovider: mock\n", encoding="utf-8")
            answers = iter(["3", "qwen2.5:7b", "gpt-4.1-mini", "research,summary"])
            messages = []
            run_setup(config_file=config, env_file=root / ".env",
                      input_fn=lambda prompt: next(answers), secret_fn=lambda prompt: "sk-test-secret",
                      output_fn=messages.append)
            saved = config.read_text(encoding="utf-8")
            env_saved = (root / ".env").read_text(encoding="utf-8")
            self.assertNotIn("sk-test-secret", saved)
            self.assertIn("provider: hybrid", saved)
            self.assertIn("OPENAI_API_KEY=sk-test-secret", env_saved)
            self.assertIn("YUKI_CLOUD_TASKS=research,summary", env_saved)
            self.assertNotIn("sk-test-secret", "\n".join(messages))
            if os.name == "posix":
                self.assertEqual((root / ".env").stat().st_mode & 0o777, 0o600)

    def test_local_env_does_not_override_shell_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text("OPENAI_API_KEY=file-key\n", encoding="utf-8")
            previous = os.environ.get("OPENAI_API_KEY")
            os.environ["OPENAI_API_KEY"] = "shell-key"
            try:
                load_local_env(env_file)
                self.assertEqual(os.environ["OPENAI_API_KEY"], "shell-key")
            finally:
                if previous is None:
                    os.environ.pop("OPENAI_API_KEY", None)
                else:
                    os.environ["OPENAI_API_KEY"] = previous

    def test_invalid_provider_choice_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.yaml"
            config.write_text("provider: mock\n", encoding="utf-8")
            answers = iter(["9"])
            with self.assertRaises(ValueError):
                run_setup(config_file=config, env_file=Path(tmp) / ".env",
                          input_fn=lambda prompt: next(answers), secret_fn=lambda prompt: "",
                          output_fn=lambda message: None)


if __name__ == "__main__":
    unittest.main()
