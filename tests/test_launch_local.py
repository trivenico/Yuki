"""Launcher preflight tests: provider failures must precede admin credential prompts."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "launch_local.py"
SPEC = importlib.util.spec_from_file_location("yuki_launch_local", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class _Provider:
    def __init__(self, ready):
        self.ready = ready

    def is_available(self):
        return self.ready


class LaunchLocalTests(unittest.TestCase):
    def test_unavailable_provider_fails_before_admin_setup(self):
        with patch.object(MODULE, "_provider_ready", return_value=False):
            with patch("builtins.input", side_effect=AssertionError("admin prompt must not run")):
                with patch("sys.argv", ["launch_local.py", "--provider", "ollama", "--model", "qwen2.5:7b"]):
                    self.assertEqual(MODULE.main(), 1)

    def test_provider_preflight_uses_factory_without_inference(self):
        calls = []
        with patch("core.config.load_config", return_value={"provider": "openai"}) as load:
            with patch("providers.factory.create_provider", side_effect=lambda cfg, environ: calls.append((cfg, environ)) or _Provider(True)) as factory:
                self.assertTrue(MODULE._provider_ready("openai", None, {"OPENAI_API_KEY": "test-only"}))
        self.assertEqual(len(calls), 1)
        factory.assert_called_once()
        load.assert_called_once()

    def test_provider_preflight_rejects_unavailable_model(self):
        with patch("core.config.load_config", return_value={"provider": "ollama"}):
            with patch("providers.factory.create_provider", return_value=_Provider(False)):
                self.assertFalse(MODULE._provider_ready("ollama", "missing-model", {}))


if __name__ == "__main__":
    unittest.main()
