import tempfile
import unittest
from unittest.mock import patch

from core.sandbox_policy import SandboxPolicy
from core.sandbox_runtime import SandboxRuntimeError, build_docker_argv


class DockerSandboxArgvTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def argv(self, policy=None, workspace=None):
        return build_docker_argv(
            policy or SandboxPolicy(),
            image="python:3.12",
            workspace=workspace or self.tmp.name,
            command=["python", "-c", "print('hello world')"],
        )

    @patch("core.sandbox_runtime.os.getgid", return_value=1000, create=True)
    @patch("core.sandbox_runtime.os.getuid", return_value=1000, create=True)
    def test_default_workspace_mount_is_read_only(self, _uid, _gid):
        argv = self.argv()
        mount = argv[argv.index("--mount") + 1]
        self.assertTrue(mount.endswith(",ro"))
        self.assertEqual(argv[argv.index("--network") + 1], "none")
        self.assertIn("--read-only", argv)
        self.assertIn("--cap-drop", argv)
        self.assertIn("ALL", argv)
        self.assertIn("--pids-limit", argv)
        self.assertEqual(argv[-3:], ["python", "-c", "print('hello world')"])

    @patch("core.sandbox_runtime.os.getgid", return_value=1000, create=True)
    @patch("core.sandbox_runtime.os.getuid", return_value=1000, create=True)
    def test_write_mount_requires_explicit_capability(self, _uid, _gid):
        policy = SandboxPolicy(capabilities=frozenset({"write_workspace"}))
        argv = self.argv(policy)
        self.assertTrue(argv[argv.index("--mount") + 1].endswith(",rw"))

    @patch("core.sandbox_runtime.os.getgid", return_value=1000, create=True)
    @patch("core.sandbox_runtime.os.getuid", return_value=1000, create=True)
    def test_allowlist_fails_closed_without_gateway(self, _uid, _gid):
        policy = SandboxPolicy(network_mode="allowlist", capabilities=frozenset({"network_allowlist"}))
        with self.assertRaises(SandboxRuntimeError):
            self.argv(policy)

    @patch("core.sandbox_runtime.os.getgid", return_value=1000, create=True)
    @patch("core.sandbox_runtime.os.getuid", return_value=0, create=True)
    def test_root_service_is_rejected(self, _uid, _gid):
        with self.assertRaises(SandboxRuntimeError):
            self.argv()

    @patch("core.sandbox_runtime.os.getgid", return_value=1000, create=True)
    @patch("core.sandbox_runtime.os.getuid", return_value=1000, create=True)
    def test_invalid_image_and_shell_string_are_rejected(self, _uid, _gid):
        with self.assertRaises(SandboxRuntimeError):
            build_docker_argv(SandboxPolicy(), image="python; invalid", workspace=self.tmp.name,
                              command=["python", "--version"])
        with self.assertRaises(SandboxRuntimeError):
            build_docker_argv(SandboxPolicy(), image="python:3.12", workspace=self.tmp.name,
                              command="python --version")

    def test_workspace_must_be_absolute_existing_and_not_symlink(self):
        with self.assertRaises(SandboxRuntimeError):
            build_docker_argv(SandboxPolicy(), image="python:3.12", workspace="relative/path",
                              command=["python", "--version"])
        target = tempfile.TemporaryDirectory()
        self.addCleanup(target.cleanup)
        link = self.tmp.name + "-link"
        try:
            import os
            os.symlink(target.name, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation unavailable")
        self.addCleanup(lambda: os.unlink(link) if os.path.lexists(link) else None)
        with self.assertRaises(SandboxRuntimeError):
            build_docker_argv(SandboxPolicy(), image="python:3.12", workspace=link,
                              command=["python", "--version"])


if __name__ == "__main__":
    unittest.main()
