import io
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


    def test_child_environment_excludes_unrelated_application_secrets(self):
        source = {
            "PATH": "/usr/bin",
            "HOME": "/home/tester",
            "USERPROFILE": "C:\\\\Users\\\\tester",
            "OPENAI_API_KEY": "do-not-forward",
            "AWS_SECRET_ACCESS_KEY": "do-not-forward",
            "DATABASE_URL": "do-not-forward",
            "GEMINI_API_KEY": "do-not-forward",
            "NODE_EXTRA_CA_CERTS": "/tmp/custom-ca.pem",
        }
        env = GeminiCLIProvider._cli_environment(source)
        self.assertEqual(env["PATH"], "/usr/bin")
        self.assertEqual(env["HOME"], "/home/tester")
        self.assertEqual(env["NODE_EXTRA_CA_CERTS"], "/tmp/custom-ca.pem")
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", env)
        self.assertNotIn("DATABASE_URL", env)
        self.assertNotIn("GEMINI_API_KEY", env)

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
            fake_process.stdout = io.BytesIO(b"abcdefgh")
            return fake_process

        with patch("providers.gemini_cli.shutil.which", return_value="/usr/bin/gemini"), \
             patch("providers.gemini_cli.subprocess.Popen", side_effect=fake_popen) as popen:
            result = provider.generate("hello")
        self.assertEqual(result, "abcde\n[Response truncated by Yuki.]")
        args, kwargs = popen.call_args
        self.assertEqual(args[0][0], "/usr/bin/gemini")
        self.assertIn("--sandbox", args[0])
        self.assertIn("--approval-mode", args[0])
        self.assertNotIn("--prompt", args[0])
        self.assertNotIn("hello", args[0])
        self.assertIs(kwargs["stdin"], subprocess.PIPE)
        fake_process.stdin.write.assert_called_once_with(b"hello")
        fake_process.stdin.close.assert_called_once_with()
        self.assertIs(kwargs["shell"], False)
        self.assertNotIn("capture_output", kwargs)

    def test_prompt_pipe_failure_stops_spawned_process(self):
        provider = GeminiCLIProvider()
        fake_process = MagicMock()
        fake_process.pid = 12345
        fake_process.stdin.write.side_effect = BrokenPipeError("private pipe detail")

        with patch("providers.gemini_cli.shutil.which", return_value="/usr/bin/gemini"), \
             patch("providers.gemini_cli.subprocess.Popen", return_value=fake_process), \
             patch.object(provider, "_stop_process") as stop_process:
            with self.assertRaisesRegex(RuntimeError, "prompt could not be delivered safely") as caught:
                provider.generate("sensitive prompt")

        self.assertNotIn("sensitive prompt", str(caught.exception))
        stop_process.assert_called_once_with(fake_process)

    def test_subprocess_output_is_drained_but_only_bounded_prefix_is_retained(self):
        provider = GeminiCLIProvider(max_output_chars=5)
        fake_process = MagicMock()
        fake_process.pid = 12345
        fake_process.wait.return_value = 0
        fake_process.stdout = io.BytesIO(b"x" * 100_000)

        with patch("providers.gemini_cli.shutil.which", return_value="/usr/bin/gemini"), \
             patch("providers.gemini_cli.subprocess.Popen", return_value=fake_process) as popen:
            result = provider.generate("hello")

        self.assertEqual(result, "xxxxx\n[Response truncated by Yuki.]")
        self.assertIs(popen.call_args.kwargs["stdout"], subprocess.PIPE)
        self.assertIs(popen.call_args.kwargs["stderr"], subprocess.DEVNULL)

    def test_timeout_kills_process_and_returns_sanitized_error(self):
        provider = GeminiCLIProvider(timeout=1)
        fake_process = MagicMock()
        fake_process.pid = 12345
        fake_process.stdout = io.BytesIO(b"")
        fake_process.wait.side_effect = [subprocess.TimeoutExpired("gemini", 1), 0]
        with patch("providers.gemini_cli.shutil.which", return_value="gemini.exe" if os.name == "nt" else "/usr/bin/gemini"), \
             patch("providers.gemini_cli.subprocess.Popen", return_value=fake_process):
            if os.name == "posix":
                with patch("providers.gemini_cli.os.killpg") as killpg:
                    with self.assertRaisesRegex(TimeoutError, "deadline"):
                        provider.generate("secret prompt")
                killpg.assert_called_once_with(12345, __import__("signal").SIGKILL)
            else:
                with patch.object(fake_process, "kill") as kill:
                    with self.assertRaisesRegex(TimeoutError, "deadline"):
                        provider.generate("secret prompt")
                kill.assert_called_once()
        fake_process.wait.assert_called()

    def test_reader_start_failure_stops_spawned_process(self):
        provider = GeminiCLIProvider()
        fake_process = MagicMock()
        fake_process.pid = 12345
        fake_process.stdout = io.BytesIO(b"")

        with patch("providers.gemini_cli.shutil.which", return_value="/usr/bin/gemini"), \
             patch("providers.gemini_cli.subprocess.Popen", return_value=fake_process), \
             patch("providers.gemini_cli.threading.Thread.start", side_effect=RuntimeError("thread unavailable")), \
             patch.object(provider, "_stop_process") as stop_process:
            with self.assertRaisesRegex(RuntimeError, "output reader"):
                provider.generate("hello")

        stop_process.assert_called_once_with(fake_process)
        fake_process.wait.assert_not_called()

    def test_unexpected_wait_failure_stops_spawned_process(self):
        provider = GeminiCLIProvider()
        fake_process = MagicMock()
        fake_process.pid = 12345
        fake_process.stdout = io.BytesIO(b"")
        fake_process.wait.side_effect = OSError("wait failed")

        with patch("providers.gemini_cli.shutil.which", return_value="/usr/bin/gemini"), \
             patch("providers.gemini_cli.subprocess.Popen", return_value=fake_process), \
             patch.object(provider, "_stop_process") as stop_process:
            with self.assertRaisesRegex(OSError, "wait failed"):
                provider.generate("hello")

        stop_process.assert_called_once_with(fake_process)

    def test_stdout_pipe_is_closed_after_successful_drain(self):
        provider = GeminiCLIProvider()
        fake_process = MagicMock()
        fake_process.pid = 12345
        fake_process.wait.return_value = 0
        stream = io.BytesIO(b"complete response")
        fake_process.stdout = stream

        with patch("providers.gemini_cli.shutil.which", return_value="/usr/bin/gemini"), \
             patch("providers.gemini_cli.subprocess.Popen", return_value=fake_process):
            self.assertEqual(provider.generate("hello"), "complete response")

        self.assertTrue(stream.closed)

    def test_stdout_close_error_rejects_partial_response(self):
        class CloseFailureStream:
            def __init__(self):
                self._read = False

            def read(self, _size):
                if self._read:
                    return b""
                self._read = True
                return b"apparently complete response"

            def close(self):
                raise OSError("private close detail")

        provider = GeminiCLIProvider()
        fake_process = MagicMock()
        fake_process.pid = 12345
        fake_process.stdout = CloseFailureStream()
        fake_process.wait.return_value = 0

        with patch("providers.gemini_cli.shutil.which", return_value="/usr/bin/gemini"), \
             patch("providers.gemini_cli.subprocess.Popen", return_value=fake_process):
            with self.assertRaisesRegex(RuntimeError, "could not be read completely") as caught:
                provider.generate("hello")

        self.assertNotIn("private close detail", str(caught.exception))

    def test_stdout_reader_error_rejects_partial_response(self):
        class BrokenStream:
            def read(self, _size):
                raise OSError("private pipe detail")

        provider = GeminiCLIProvider()
        fake_process = MagicMock()
        fake_process.pid = 12345
        fake_process.stdout = BrokenStream()
        fake_process.wait.return_value = 0

        with patch("providers.gemini_cli.shutil.which", return_value="/usr/bin/gemini"), \
             patch("providers.gemini_cli.subprocess.Popen", return_value=fake_process):
            with self.assertRaisesRegex(RuntimeError, "could not be read completely") as caught:
                provider.generate("hello")

        self.assertNotIn("private pipe detail", str(caught.exception))

    def test_stdout_reader_value_error_rejects_partial_response(self):
        class BrokenStream:
            def read(self, _size):
                raise ValueError("private stream detail")

        provider = GeminiCLIProvider()
        fake_process = MagicMock()
        fake_process.pid = 12345
        fake_process.stdout = BrokenStream()
        fake_process.wait.return_value = 0

        with patch("providers.gemini_cli.shutil.which", return_value="/usr/bin/gemini"), \
             patch("providers.gemini_cli.subprocess.Popen", return_value=fake_process):
            with self.assertRaisesRegex(RuntimeError, "could not be read completely") as caught:
                provider.generate("hello")

        self.assertNotIn("private stream detail", str(caught.exception))

    def test_nonzero_exit_does_not_disclose_stderr(self):
        provider = GeminiCLIProvider()
        fake_process = MagicMock()
        fake_process.pid = 12345
        fake_process.wait.return_value = 2

        def fake_popen(args, **kwargs):
            fake_process.stdout = io.BytesIO(b"")
            self.assertIs(kwargs["stderr"], subprocess.DEVNULL)
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
