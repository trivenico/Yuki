"""Regression tests for safe provider selection defaults and local smoke checks."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from core.config import load_config


class ProviderDefaultTests(unittest.TestCase):
    def test_minimal_config_defaults_to_real_provider_not_mock(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.yaml"
            config_path.write_text("workspace: ./workspace\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                config = load_config(config_path)
        self.assertEqual(config["provider"], "ollama")
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

    def test_ollama_provider_redirect_handler_blocks_redirects(self):
        from providers.ollama import _NoRedirectHandler

        handler = _NoRedirectHandler()
        self.assertIsNone(handler.redirect_request(
            None, None, 302, "Found", {}, "https://remote.example.invalid/collect"
        ))

    def test_ollama_smoke_test_refuses_non_ollama_provider(self):
        from scripts import smoke_test_ollama

        with (
            patch.object(smoke_test_ollama, "load_config", return_value={"provider": "openai"}),
            patch("builtins.print") as output,
        ):
            self.assertEqual(smoke_test_ollama.main(), 2)
        self.assertTrue(any("No request was sent" in str(call) for call in output.call_args_list))

    def test_ollama_smoke_test_rejects_non_sentinel_response(self):
        from scripts import smoke_test_ollama

        config = {"provider": "ollama", "ollama_model": "test-model"}
        provider = Mock()
        provider.is_available.return_value = True
        provider.generate.return_value = "I could not follow that instruction."
        with (
            patch.object(smoke_test_ollama, "load_config", return_value=config),
            patch.object(smoke_test_ollama, "create_provider", return_value=provider),
            patch("builtins.print"),
        ):
            self.assertEqual(smoke_test_ollama.main(), 7)
        provider.generate.assert_called_once()

    def test_ollama_smoke_test_runs_provider_contract(self):
        from scripts import smoke_test_ollama

        config = {"provider": "ollama", "ollama_model": "test-model"}
        provider = Mock()
        provider.is_available.return_value = True
        provider.generate.return_value = "YUKI_OLLAMA_SMOKE_OK"
        with (
            patch.object(smoke_test_ollama, "load_config", return_value=config),
            patch.object(smoke_test_ollama, "create_provider", return_value=provider),
            patch("builtins.print"),
        ):
            self.assertEqual(smoke_test_ollama.main(), 0)
        provider.is_available.assert_called_once_with()
        provider.generate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
