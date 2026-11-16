"""Regression tests for the guarded Docker sandbox wrapper."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hackback.sandbox import DockerSandbox, SandboxError


class DockerSandboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.image = "registry.example/yuki-runner@sha256:" + "a" * 64

    def make_sandbox(self, *, approver=None):
        return DockerSandbox(self.tmp.name, allowed_images=(self.image,),
                             enabled=True, approver=approver or (lambda _: True))

    @patch("hackback.sandbox.subprocess.run")
    def test_workspace_is_read_only_by_default(self, run):
        run.return_value.returncode = 0
        run.return_value.stdout = "ok"
        run.return_value.stderr = ""
        self.make_sandbox().run(self.image, ["python", "-c", "print('ok')"])
        command = run.call_args.args[0]
        mount = command[command.index("--mount") + 1]
        self.assertTrue(mount.endswith(",ro"))

    @patch("hackback.sandbox.subprocess.run")
    def test_workspace_write_requires_explicit_opt_in(self, run):
        run.return_value.returncode = 0
        run.return_value.stdout = "ok"
        run.return_value.stderr = ""
        self.make_sandbox().run(self.image, ["python", "-c", "pass"], write_workspace=True)
        command = run.call_args.args[0]
        mount = command[command.index("--mount") + 1]
        self.assertTrue(mount.endswith(",rw"))

    def test_comma_in_workspace_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as parent:
            path = Path(parent) / "workspace,ro"
            path.mkdir()
            with self.assertRaisesRegex(SandboxError, "mount syntax"):
                DockerSandbox(path)

    def test_root_workspace_is_rejected(self):
        with self.assertRaises(SandboxError):
            DockerSandbox(Path(tempfile.gettempdir()).anchor)


if __name__ == "__main__":
    unittest.main()
