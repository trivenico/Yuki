import os
import subprocess
import unittest
from unittest.mock import patch, MagicMock

from providers.gemini_cli import GeminiCLIProvider


class GeminiCLIProviderTests(unittest.TestCase):
    def test_only_official_executable_is_allowed(self):
        with self.assertRaisesRegex(ValueError, "official"):
            GeminiCLIProvider(executable="/tmp/fake-gemini")

    def test_validates_prompt_and_limits(self):
        provider = GeminiCLIProvider(max_prompt_chars=3)
        with self.assertRaisesRegex(ValueError, "non-empty"):
            provider.generate("  ")
        with self.assertRaisesRegex(ValueError, "character limit"):
            provider.generate("four")

    def test_availability_checks_installation(self):
        with patch("providers.gemini_cli.shutil.which", return_value="/usr/bin/gemini"):
            self.assertTrue(GeminiCLIProvider().is_available())
        with patch("providers.gemini_cli.shutil.which", return_value=None):
            self.assertFalse(GeminiCLIProvider().is_available())

    def test_bounded_disk_capture_and_sandbox_arguments(self):
        provider = GeminiCLIProvider(max_output_chars=5)
        fake_process = MagicMock()
        fake_process.pid = 12345
        fake_process.wait.return_value = 0

        def fake_popen(args, **kwargs):
            kwargs["stdout"].write(b"abcdefgh")
            return fake_process

        with patch("providers.gemini_cli.shutil.which", return_value="/usr/bin/gemini"), \
             patch("providers.gemini_cli.subprocess.Popen", side_effect=fake_popen) as popen:
            result = provider.generate("hello")
        self.assertEqual(result, "abcde\n[Response truncated by Yuki.]")
        args, kwargs = popen.call_args
        self.assertEqual(args[0][0], "/usr/bin/gemini")
        self.assertIn("--sandbox", args[0])
        self.assertIn("--approval-mode", args[0])
        self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
        self.assertIs(kwargs["shell"], False)
        self.assertNotIn("capture_output", kwargs)

    def test_timeout_kills_process_and_returns_sanitized_error(self):
        provider = GeminiCLIProvider(timeout=1)
        fake_process = MagicMock()
        fake_process.pid = 12345
        fake_process.wait.side_effect = [subprocess.TimeoutExpired("gemini", 1), 0]
        with patch("providers.gemini_cli.shutil.which", return_value="/usr/bin/gemini"), \
             patch("providers.gemini_cli.subprocess.Popen", return_value=fake_process), \
             patch("providers.gemini_cli.os.killpg") as killpg:
            with self.assertRaisesRegex(TimeoutError, "deadline"):
                provider.generate("secret prompt")
        if os.name == "posix":
            killpg.assert_called_once_with(12345, __import__("signal").SIGKILL)
        fake_process.wait.assert_called()

    def test_nonzero_exit_does_not_disclose_stderr(self):
        provider = GeminiCLIProvider()
        fake_process = MagicMock()
        fake_process.pid = 12345
        fake_process.wait.return_value = 2

        def fake_popen(args, **kwargs):
            kwargs["stderr"].write(b"secret-token /private/path")
            return fake_process

        with patch("providers.gemini_cli.shutil.which", return_value="/usr/bin/gemini"), \
             patch("providers.gemini_cli.subprocess.Popen", side_effect=fake_popen):
            with self.assertRaises(RuntimeError) as ctx:
                provider.generate("hello")
        self.assertNotIn("secret-token", str(ctx.exception))
        self.assertNotIn("/private/path", str(ctx.exception))

    def test_factory_and_registry_wiring(self):
        from providers.factory import create_provider
        from yuki_providers import ProviderManager
        provider = create_provider({"provider": "gemini"})
        self.assertIsInstance(provider, GeminiCLIProvider)
        manager = ProviderManager()
        listed = {item["name"]: item for item in manager.list_providers()}
        self.assertTrue(listed["gemini"]["implemented"])
        self.assertIn("gemini", listed["gemini"]["auth_methods"][0])
        self.assertIn("gemini", manager.select("gemini").lower())
        self.assertIsInstance(manager.create_selected_adapter(), GeminiCLIProvider)


if __name__ == "__main__":
    unittest.main()
