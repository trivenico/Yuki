import tempfile
import unittest
from unittest.mock import patch
from core.sandbox_policy import SandboxPolicy
from core.sandbox_runtime import SandboxRuntimeError, build_docker_argv

class DockerSandboxArgvTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    @patch("core.sandbox_runtime.os.getgid", return_value=1000, create=True)
    @patch("core.sandbox_runtime.os.getuid", return_value=1000, create=True)
    def test_restrictive_flags_and_argument_vector(self, _uid, _gid):
        argv = build_docker_argv(SandboxPolicy(), image="python:3.12",
            workspace=self.tmp.name, command=["python", "-c", "print('hello world')"])
        self.assertEqual(argv[argv.index("--network") + 1], "none")
        self.assertIn("--read-only", argv)
        self.assertIn("--cap-drop", argv)
        self.assertIn("ALL", argv)
        self.assertIn("--pids-limit", argv)
        self.assertEqual(argv[-3:], ["python", "-c", "print('hello world')"])

    @patch("core.sandbox_runtime.os.getgid", return_value=1000, create=True)
    @patch("core.sandbox_runtime.os.getuid", return_value=1000, create=True)
    def test_allowlist_fails_closed_without_gateway(self, _uid, _gid):
        policy = SandboxPolicy(network_mode="allowlist",
            capabilities=frozenset({"network_allowlist"}))
        with self.assertRaises(SandboxRuntimeError):
            build_docker_argv(policy, image="python:3.12",
                workspace=self.tmp.name, command=["python", "--version"])

    @patch("core.sandbox_runtime.os.getgid", return_value=1000, create=True)
    @patch("core.sandbox_runtime.os.getuid", return_value=0, create=True)
    def test_root_service_is_rejected(self, _uid, _gid):
        with self.assertRaises(SandboxRuntimeError):
            build_docker_argv(SandboxPolicy(), image="python:3.12",
                workspace=self.tmp.name, command=["python", "--version"])

    @patch("core.sandbox_runtime.os.getgid", return_value=1000, create=True)
    @patch("core.sandbox_runtime.os.getuid", return_value=1000, create=True)
    def test_invalid_image_and_shell_string_are_rejected(self, _uid, _gid):
        with self.assertRaises(SandboxRuntimeError):
            build_docker_argv(SandboxPolicy(), image="python; invalid",
                workspace=self.tmp.name, command=["python", "--version"])
        with self.assertRaises(SandboxRuntimeError):
            build_docker_argv(SandboxPolicy(), image="python:3.12",
                workspace=self.tmp.name, command="python --version")

    def test_workspace_must_be_absolute_and_exist(self):
        with self.assertRaises(SandboxRuntimeError):
            build_docker_argv(SandboxPolicy(), image="python:3.12",
                workspace="relative/path", command=["python", "--version"])

if __name__ == "__main__":
    unittest.main()
