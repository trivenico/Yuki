"""Adversarial regression tests for bounded Docker sandbox output."""
import io
import subprocess
import tempfile
import unittest
from unittest import mock

from hackback.sandbox import DockerSandbox, SandboxError


IMAGE = "example.invalid/yuki-worker@sha256:" + "a" * 64


class FakeProcess:
    def __init__(self, stdout: bytes, stderr: bytes, returncode: int = 0):
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self.returncode = returncode
        self.killed = False

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.killed = True


class SandboxOutputBoundsTests(unittest.TestCase):
    def test_oversized_stdout_and_stderr_are_bounded_and_marked(self):
        process = FakeProcess(b"A" * 100_000, b"B" * 90_000)
        with tempfile.TemporaryDirectory() as directory:
            sandbox = DockerSandbox(
                directory,
                allowed_images=[IMAGE],
                enabled=True,
                approver=lambda _reason: True,
                max_output_bytes=1024,
            )
            with mock.patch("hackback.sandbox.subprocess.Popen", return_value=process) as popen:
                result = sandbox.run(IMAGE, ["worker"])

        self.assertEqual(result["returncode"], 0)
        self.assertEqual(len(result["stdout"].encode("utf-8")), 1024)
        self.assertEqual(len(result["stderr"].encode("utf-8")), 1024)
        self.assertTrue(result["stdout_truncated"])
        self.assertTrue(result["stderr_truncated"])
        self.assertEqual(result["stdout"], "A" * 1024)
        self.assertEqual(result["stderr"], "B" * 1024)
        args, kwargs = popen.call_args
        self.assertFalse(kwargs["shell"] if "shell" in kwargs else False)
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertNotIn("sh", args[0][0].lower())

    def test_approval_denial_prevents_process_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            sandbox = DockerSandbox(
                directory,
                allowed_images=[IMAGE],
                enabled=True,
                approver=lambda _reason: False,
            )
            with mock.patch("hackback.sandbox.subprocess.Popen") as popen:
                with self.assertRaisesRegex(SandboxError, "approval"):
                    sandbox.run(IMAGE, ["worker"])
        popen.assert_not_called()

    def test_attempt_to_override_docker_network_policy_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            sandbox = DockerSandbox(
                directory,
                allowed_images=[IMAGE],
                enabled=True,
                approver=lambda _reason: True,
            )
            with mock.patch("hackback.sandbox.subprocess.Popen") as popen:
                with self.assertRaisesRegex(SandboxError, "security flags"):
                    sandbox.run(IMAGE, ["worker", "--network", "host"])
        popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
