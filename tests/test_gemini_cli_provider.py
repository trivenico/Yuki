import subprocess
import unittest
from unittest.mock import patch

from providers.factory import create_provider
from providers.gemini_cli import GeminiCLIProvider


class GeminiCLIProviderTests(unittest.TestCase):
    def test_factory_creates_account_cli_provider(self):
        provider = create_provider(
            {"provider": "gemini_cli"},
            environ={"YUKI_PROVIDER_TIMEOUT_SECONDS": "12"},
        )
        self.assertIsInstance(provider, GeminiCLIProvider)
        self.assertEqual(provider.timeout, 12.0)

    def test_installed_cli_is_reported_available(self):
        provider = GeminiCLIProvider()
        with patch("providers.gemini_cli.shutil.which", return_value="/usr/bin/gemini"):
            self.assertTrue(provider.is_available())

    def test_missing_cli_is_reported_unavailable(self):
        provider = GeminiCLIProvider()
        with patch("providers.gemini_cli.shutil.which", return_value=None):
            self.assertFalse(provider.is_available())
            with self.assertRaisesRegex(RuntimeError, "not installed"):
                provider.generate("hello")

    def test_generate_uses_noninteractive_safe_arguments_and_temp_cwd(self):
        provider = GeminiCLIProvider(timeout=10)
        completed = subprocess.CompletedProcess(
            args=["gemini"], returncode=0, stdout="Hello from Gemini", stderr=""
        )
        with patch("providers.gemini_cli.shutil.which", return_value="/usr/bin/gemini"), patch(
            "providers.gemini_cli.subprocess.run", return_value=completed
        ) as run:
            self.assertEqual(provider.generate("Say hello"), "Hello from Gemini")
        args, kwargs = run.call_args
        self.assertEqual(args[0][0], "/usr/bin/gemini")
        self.assertIn("--prompt", args[0])
        self.assertIn("--approval-mode", args[0])
        self.assertEqual(kwargs["shell"], False)
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertTrue(kwargs["cwd"].startswith("/tmp/yuki-gemini-") or "yuki-gemini-" in kwargs["cwd"])
        self.assertEqual(kwargs["timeout"], 10.0)

    def test_timeout_is_reported_without_leaking_subprocess_details(self):
        provider = GeminiCLIProvider(timeout=1)
        with patch("providers.gemini_cli.shutil.which", return_value="/usr/bin/gemini"), patch(
            "providers.gemini_cli.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="gemini", timeout=1),
        ):
            with self.assertRaisesRegex(TimeoutError, "deadline"):
                provider.generate("hello")

    def test_nonzero_exit_does_not_echo_stderr(self):
        provider = GeminiCLIProvider()
        completed = subprocess.CompletedProcess(
            args=["gemini"], returncode=7, stdout="", stderr="secret-token /private/path"
        )
        with patch("providers.gemini_cli.shutil.which", return_value="/usr/bin/gemini"), patch(
            "providers.gemini_cli.subprocess.run", return_value=completed
        ):
            with self.assertRaises(RuntimeError) as caught:
                provider.generate("hello")
        self.assertNotIn("secret-token", str(caught.exception))
        self.assertNotIn("/private/path", str(caught.exception))

    def test_rejects_empty_or_oversized_prompts(self):
        provider = GeminiCLIProvider(max_prompt_chars=5)
        with self.assertRaises(ValueError):
            provider.generate(" ")
        with self.assertRaisesRegex(ValueError, "character limit"):
            provider.generate("123456")

    def test_timeout_validation(self):
        for value in (0, 121, True, "10"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    GeminiCLIProvider(timeout=value)


if __name__ == "__main__":
    unittest.main()
