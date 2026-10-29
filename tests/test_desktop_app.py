import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ui.desktop_app import CommandHistory, ROOT, build_runtime_command, provider_environment_override


class DesktopAppTests(unittest.TestCase):
    def test_desktop_uses_the_current_python_and_authoritative_runtime(self):
        command = build_runtime_command()
        self.assertEqual(command[0], sys.executable)
        self.assertEqual(command[1], "-u")
        self.assertEqual(Path(command[2]), ROOT / "main.py")
        self.assertNotIn("shell", command)

    def test_packaged_desktop_uses_adjacent_runtime_executable(self):
        with tempfile.TemporaryDirectory() as temporary:
            install_root = Path(temporary)
            desktop_dir = install_root / "YukiDesktop"
            runtime_dir = install_root / "YukiRuntime"
            desktop_dir.mkdir()
            runtime_dir.mkdir()
            desktop = desktop_dir / "YukiDesktop.exe"
            runtime_name = "YukiRuntime.exe" if os.name == "nt" else "YukiRuntime"
            runtime = runtime_dir / runtime_name
            desktop.touch()
            runtime.touch()
            with patch.object(sys, "frozen", True, create=True), patch.object(
                sys, "executable", str(desktop)
            ):
                self.assertEqual(build_runtime_command(), [str(runtime.resolve())])

    def test_packaged_desktop_reports_missing_runtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            install_root = Path(temporary)
            desktop_dir = install_root / "YukiDesktop"
            desktop_dir.mkdir()
            desktop = desktop_dir / "YukiDesktop.exe"
            desktop.touch()
            with patch.object(sys, "frozen", True, create=True), patch.object(
                sys, "executable", str(desktop)
            ):
                with self.assertRaisesRegex(RuntimeError, "not found in the adjacent"):
                    build_runtime_command()

    def test_provider_environment_override_warns_when_provider_differs(self):
        message = provider_environment_override(
            "ollama",
            "llama3.2:3b",
            environ={"YUKI_PROVIDER": "openai"},
        )
        self.assertIn("YUKI_PROVIDER", message)
        self.assertIn("openai", message)
        self.assertIn("ollama", message)

    def test_provider_environment_override_warns_when_model_differs(self):
        message = provider_environment_override(
            "ollama",
            "llama3.2:3b",
            environ={"YUKI_OLLAMA_MODEL": "qwen2.5:7b"},
        )
        self.assertIn("YUKI_OLLAMA_MODEL", message)
        self.assertIn("qwen2.5:7b", message)

    def test_provider_environment_override_allows_matching_values(self):
        message = provider_environment_override(
            "ollama",
            "llama3.2:3b",
            environ={
                "YUKI_PROVIDER": "ollama",
                "YUKI_OLLAMA_MODEL": "llama3.2:3b",
            },
        )
        self.assertIsNone(message)

    def test_provider_environment_override_warns_for_gemini_and_hybrid_models(self):
        cases = (
            ("gemini", "YUKI_GEMINI_MODEL", "gemini-2.5-pro"),
            ("hybrid", "YUKI_CLOUD_MODEL", "cloud-model"),
        )
        for provider, variable, configured_model in cases:
            with self.subTest(provider=provider):
                message = provider_environment_override(
                    provider,
                    "ui-model",
                    environ={"YUKI_PROVIDER": provider, variable: configured_model},
                )
                self.assertIn(variable, message)
                self.assertIn(configured_model, message)
                self.assertIn("ignore", message)

    def test_provider_environment_override_ignores_unrelated_model_variables(self):
        message = provider_environment_override(
            "ollama",
            "llama3.2:3b",
            environ={"YUKI_OPENAI_MODEL": "gpt-4.1-mini"},
        )
        self.assertIsNone(message)

    def test_command_history_moves_backward_and_forward(self):
        history = CommandHistory()
        self.assertEqual(history.move(-1), "")
        history.add("/help")
        history.add("/status")
        self.assertEqual(history.move(-1), "/status")
        self.assertEqual(history.move(-1), "/help")
        self.assertEqual(history.move(1), "/status")
        self.assertEqual(history.move(1), "")

    def test_command_history_deduplicates_adjacent_commands_and_is_bounded(self):
        history = CommandHistory(limit=2)
        history.add("/help")
        history.add("/help")
        history.add("/status")
        history.add("/readiness")
        self.assertEqual(history.move(-1), "/readiness")
        self.assertEqual(history.move(-1), "/status")
        self.assertEqual(history.move(-1), "")

    def test_command_history_rejects_invalid_direction_and_limit(self):
        with self.assertRaisesRegex(ValueError, "positive"):
            CommandHistory(limit=0)
        with self.assertRaisesRegex(ValueError, "direction"):
            CommandHistory().move(0)

    def test_desktop_root_is_the_repository_root(self):
        self.assertTrue((ROOT / "main.py").is_file())
        self.assertTrue((ROOT / "yuki_cli.py").is_file())


if __name__ == "__main__":
    unittest.main()
