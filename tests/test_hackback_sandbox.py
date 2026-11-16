"""Regression tests for the guarded Docker sandbox wrapper."""
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hackback.sandbox import DockerSandbox, SandboxError


class FakeProcess:
    def __init__(self, stdout=b"ok", stderr=b""):
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self.returncode = 0
        self.terminated = False

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.terminated = True


class DockerSandboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.image = "registry.example/yuki-runner@sha256:" + "a" * 64

    def make_sandbox(self, *, approver=None):
        return DockerSandbox(self.tmp.name, allowed_images=(self.image,),
                             enabled=True, approver=approver or (lambda _: True))

    @patch("hackback.sandbox.subprocess.Popen")
    def test_workspace_is_read_only_by_default(self, popen):
        popen.return_value = FakeProcess()
        self.make_sandbox().run(self.image, ["python", "-c", "print('ok')"])
        command = popen.call_args.args[0]
        mount = command[command.index("--mount") + 1]
        self.assertTrue(mount.endswith(",ro"))

    @patch("hackback.sandbox.subprocess.Popen")
    def test_workspace_write_requires_explicit_opt_in(self, popen):
        popen.return_value = FakeProcess()
        self.make_sandbox().run(self.image, ["python", "-c", "pass"], write_workspace=True)
        command = popen.call_args.args[0]
        mount = command[command.index("--mount") + 1]
        self.assertTrue(mount.endswith(",rw"))

    @patch("hackback.sandbox.subprocess.run")
    @patch("hackback.sandbox.subprocess.Popen")
    def test_output_is_bounded_and_container_cleanup_is_requested(self, popen, docker_run):
        process = FakeProcess(stdout=b"x" * 128)
        def start(command, **_kwargs):
            cidfile = command[command.index("--cidfile") + 1]
            Path(cidfile).write_text("a" * 64, encoding="ascii")
            return process
        popen.side_effect = start
        with self.assertRaisesRegex(SandboxError, "output exceeded"):
            self.make_sandbox().run(self.image, ["python", "-c", "pass"], max_output_bytes=32)
        self.assertTrue(process.terminated)
        cleanup_command = docker_run.call_args.args[0]
        self.assertEqual(cleanup_command, ["docker", "rm", "--force", "a" * 64])
        self.assertFalse(docker_run.call_args.kwargs["shell"])

    @patch("hackback.sandbox.subprocess.Popen")
    def test_container_id_file_is_passed_to_docker(self, popen):
        popen.return_value = FakeProcess()
        self.make_sandbox().run(self.image, ["python", "--version"])
        command = popen.call_args.args[0]
        self.assertIn("--cidfile", command)
        self.assertTrue(command[command.index("--cidfile") + 1].endswith("container.id"))

    def test_invalid_output_limit_is_rejected(self):
        with self.assertRaisesRegex(SandboxError, "max_output_bytes"):
            self.make_sandbox().run(self.image, ["python"], max_output_bytes=True)

    def test_comma_in_workspace_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as parent:
            path = Path(parent) / "workspace,ro"
            path.mkdir()
            with self.assertRaisesRegex(SandboxError, "mount syntax"):
                DockerSandbox(path)

    def test_root_workspace_is_rejected(self):
        with self.assertRaises(SandboxError):
            DockerSandbox(Path(tempfile.gettempdir()).anchor)

    @patch("hackback.sandbox.subprocess.Popen")
    def test_legacy_wrapper_uses_canonical_runtime_builder(self, popen):
        popen.return_value = FakeProcess()
        sandbox = DockerSandbox(
            self.tmp.name, allowed_images=(self.image,), enabled=True,
            approver=lambda _: True, docker_binary="/opt/docker",
        )
        sandbox.run(self.image, ["python", "--version"])
        command = popen.call_args.args[0]
        self.assertEqual(command[0], "/opt/docker")
        self.assertEqual(command[command.index("--user") + 1], "65534:65534")
        self.assertEqual(command[command.index("--network") + 1], "none")
        self.assertIn("--pull=never", command)


if __name__ == "__main__":
    unittest.main()
