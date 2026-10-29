"""Offline regression tests for the explicit Ollama inference smoke script."""
import contextlib
import io
import os
import unittest
from unittest.mock import Mock, patch

from scripts import smoke_test_ollama


class OllamaSmokeScriptTests(unittest.TestCase):
    def run_main(self, config, provider=None, *, load_error=None, create_error=None, argv=None):
        output = io.StringIO()
        load_patch = (
            patch.object(smoke_test_ollama, "load_config", side_effect=load_error)
            if load_error
            else patch.object(smoke_test_ollama, "load_config", return_value=config)
        )
        create_patch = (
            patch.object(smoke_test_ollama, "create_provider", side_effect=create_error)
            if create_error
            else patch.object(smoke_test_ollama, "create_provider", return_value=provider)
        )
        with load_patch, create_patch, contextlib.redirect_stdout(output):
            code = smoke_test_ollama.main([] if argv is None else argv)
        return code, output.getvalue()

    def test_non_ollama_provider_is_rejected_before_provider_creation(self):
        with patch.object(smoke_test_ollama, "load_config", return_value={"provider": "openai"}), \
             patch.object(smoke_test_ollama, "create_provider") as create, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            code = smoke_test_ollama.main([])
        self.assertEqual(code, 2)
        self.assertIn("No request was sent", output.getvalue())
        create.assert_not_called()

    def test_missing_model_is_rejected(self):
        code, output = self.run_main({
            "provider": "ollama", "ollama_model": " ", "ollama_base_url": "http://127.0.0.1:11434"
        })
        self.assertEqual(code, 2)
        self.assertIn("no Ollama model", output)

    def test_missing_endpoint_is_rejected(self):
        code, output = self.run_main({"provider": "ollama", "ollama_model": "llama3.2:3b"})
        self.assertEqual(code, 2)
        self.assertIn("no Ollama endpoint", output)

    def test_unavailable_provider_does_not_generate_or_fallback(self):
        provider = Mock()
        provider.is_available.return_value = False
        code, output = self.run_main(
            {"provider": "ollama", "ollama_model": "llama3.2:3b", "ollama_base_url": "http://127.0.0.1:11434"},
            provider,
        )
        self.assertEqual(code, 4)
        self.assertIn("No cloud or mock fallback", output)
        provider.generate.assert_not_called()

    def test_success_requires_expected_sentinel(self):
        provider = Mock()
        provider.is_available.return_value = True
        provider.generate.return_value = "YUKI_OLLAMA_SMOKE_OK"
        code, output = self.run_main(
            {"provider": "ollama", "ollama_model": "llama3.2:3b", "ollama_base_url": "http://127.0.0.1:11434"},
            provider,
        )
        self.assertEqual(code, 0)
        self.assertIn("PASS: real Ollama inference", output)
        provider.generate.assert_called_once()

    def test_non_sentinel_response_fails(self):
        provider = Mock()
        provider.is_available.return_value = True
        provider.generate.return_value = "some unrelated answer"
        code, output = self.run_main(
            {"provider": "ollama", "ollama_model": "llama3.2:3b", "ollama_base_url": "http://127.0.0.1:11434"},
            provider,
        )
        self.assertEqual(code, 7)
        self.assertIn("sentinel was absent", output)

    def test_inference_exception_is_reported_without_traceback(self):
        provider = Mock()
        provider.is_available.return_value = True
        provider.generate.side_effect = TimeoutError("sensitive details should not leak")
        code, output = self.run_main(
            {"provider": "ollama", "ollama_model": "llama3.2:3b", "ollama_base_url": "http://127.0.0.1:11434"},
            provider,
        )
        self.assertEqual(code, 5)
        self.assertNotIn("sensitive details", output)
        self.assertNotIn("Traceback", output)

    def test_config_load_failure_is_sanitized(self):
        code, output = self.run_main({}, load_error=RuntimeError("private path"))
        self.assertEqual(code, 1)
        self.assertIn("RuntimeError", output)
        self.assertNotIn("private path", output)

    def test_explicit_overrides_are_per_invocation_and_passed_to_provider_factory(self):
        config = {
            "provider": "ollama",
            "ollama_model": "saved-model",
            "ollama_base_url": "http://127.0.0.1:11434",
        }
        provider = Mock()
        provider.is_available.return_value = False
        with patch.dict(os.environ, {}, clear=True):
            code, _ = self.run_main(
                config,
                provider,
                argv=["--model", "override-model", "--base-url", "http://127.0.0.1:11435", "--timeout", "23"],
            )
        self.assertEqual(code, 4)
        self.assertEqual(config["ollama_model"], "saved-model")
        self.assertEqual(config["ollama_base_url"], "http://127.0.0.1:11434")
        args, kwargs = smoke_test_ollama.create_provider.call_args
        self.assertEqual(args[0]["ollama_model"], "override-model")
        self.assertEqual(args[0]["ollama_base_url"], "http://127.0.0.1:11435")
        self.assertEqual(kwargs["environ"]["YUKI_OLLAMA_MODEL"], "override-model")
        self.assertEqual(kwargs["environ"]["YUKI_OLLAMA_BASE_URL"], "http://127.0.0.1:11435")
        self.assertEqual(kwargs["environ"]["YUKI_PROVIDER_TIMEOUT_SECONDS"], "23")


if __name__ == "__main__":
    unittest.main()
