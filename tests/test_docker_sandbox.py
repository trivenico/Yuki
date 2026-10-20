"""Regression tests for Docker sandbox security controls."""
import io
import tempfile
import unittest
from unittest import mock

from hackback.sandbox import DockerSandbox, SandboxError


IMAGE = "python@sha256:" + ("a" * 64)


class FakeProcess:
    def __init__(self, command, **kwargs):
        self.command = command
        self.kwargs = kwargs
        self.stdout = io.BytesIO(b'{"ok": true}')
        self.stderr = io.BytesIO(b"")
        self.returncode = 0
        self.pid = 99999999
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9


class DockerSandboxTests(unittest.TestCase):
    def sandbox(self, workspace, **kwargs):
        return DockerSandbox(
            workspace,
            allowed_images=[IMAGE],
            enabled=True,
            approver=lambda _reason: True,
            **kwargs,
        )

    def test_workspace_is_read_only_by_default_and_container_is_restricted(self):
        with tempfile.TemporaryDirectory() as directory:
            sandbox = self.sandbox(directory)
            fake = FakeProcess
            with mock.patch("hackback.sandbox.subprocess.Popen", side_effect=fake) as popen:
                result = sandbox.run(IMAGE, ["python", "-c", "print('ok')"])
            self.assertEqual(result["returncode"], 0)
            command = popen.call_args.args[0]
            self.assertIn("--network=none", command)
            self.assertIn("--read-only", command)
            self.assertIn("--cap-drop=ALL", command)
            self.assertIn("--memory-swap=512m", command)
            self.assertIn("--pids-limit=64", command)
            mount = command[command.index("--mount") + 1]
            self.assertTrue(mount.endswith(",ro"))
            self.assertFalse(popen.call_args.kwargs["shell"])

    def test_writable_workspace_requires_explicit_opt_in(self):
        with tempfile.TemporaryDirectory() as directory:
            sandbox = self.sandbox(directory, writable_workspace=True)
            with mock.patch("hackback.sandbox.subprocess.Popen", side_effect=FakeProcess) as popen:
                sandbox.run(IMAGE, ["worker"])
            mount = popen.call_args.args[0][popen.call_args.args[0].index("--mount") + 1]
            self.assertTrue(mount.endswith(",rw"))

    def test_unapproved_image_is_rejected_before_process_start(self):
        with tempfile.TemporaryDirectory() as directory:
            sandbox = self.sandbox(directory)
            with mock.patch("hackback.sandbox.subprocess.Popen") as popen:
                with self.assertRaises(SandboxError):
                    sandbox.run("python:latest", ["worker"])
            popen.assert_not_called()

    def test_root_workspace_is_rejected(self):
        with self.assertRaises(ValueError):
            DockerSandbox("/")

    def test_output_overflow_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            sandbox = self.sandbox(directory)

            class OverflowProcess(FakeProcess):
                def __init__(self, command, **kwargs):
                    super().__init__(command, **kwargs)
                    self.stdout = io.BytesIO(b"x" * (sandbox.OUTPUT_LIMIT_BYTES + 1))

            with mock.patch("hackback.sandbox.subprocess.Popen", side_effect=OverflowProcess):
                with self.assertRaisesRegex(SandboxError, "output exceeded"):
                    sandbox.run(IMAGE, ["worker"])

    def test_explicit_approval_is_required(self):
        with tempfile.TemporaryDirectory() as directory:
            sandbox = DockerSandbox(
                directory, allowed_images=[IMAGE], enabled=True, approver=lambda _reason: False
            )
            with mock.patch("hackback.sandbox.subprocess.Popen") as popen:
                with self.assertRaisesRegex(SandboxError, "approval"):
                    sandbox.run(IMAGE, ["worker"])
            popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
