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
    def test_restrictive_flags_and_read_only_workspace_default(self, _uid, _gid):
        command = ["python", "--version"]
        argv = build_docker_argv(SandboxPolicy(), image="python:3.12",
            workspace=self.tmp.name, command=command)
        self.assertEqual(argv[argv.index("--network") + 1], "none")
        self.assertIn("--read-only", argv)
        self.assertIn("--pull=never", argv)
        self.assertIn("--cap-drop", argv)
        self.assertIn("ALL", argv)
        self.assertIn("--pids-limit", argv)
        mount = argv[argv.index("--mount") + 1]
        self.assertTrue(mount.endswith(",ro"))
        self.assertEqual(argv[-2:], command)

    @patch("core.sandbox_runtime.os.getgid", return_value=1000, create=True)
    @patch("core.sandbox_runtime.os.getuid", return_value=1000, create=True)
    def test_write_workspace_capability_is_explicit(self, _uid, _gid):
        policy = SandboxPolicy(capabilities=frozenset({"write_workspace"}))
        argv = build_docker_argv(policy, image="python:3.12",
            workspace=self.tmp.name, command=["python", "--version"])
        self.assertTrue(argv[argv.index("--mount") + 1].endswith(",rw"))

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
    def test_invalid_image_and_command_are_rejected(self, _uid, _gid):
        with self.assertRaises(SandboxRuntimeError):
            build_docker_argv(SandboxPolicy(), image="python; invalid",
                workspace=self.tmp.name, command=["python", "--version"])
        with self.assertRaises(SandboxRuntimeError):
            build_docker_argv(SandboxPolicy(), image="python:3.12",
                workspace=self.tmp.name, command="python --version")
        with self.assertRaises(SandboxRuntimeError):
            build_docker_argv(SandboxPolicy(), image="python:3.12",
                workspace=self.tmp.name, command=["python", "bad\x00arg"])

    def test_workspace_path_cannot_inject_docker_mount_options(self):
        with tempfile.TemporaryDirectory(prefix="yuki,ro") as directory:
            with patch("core.sandbox_runtime.os.getuid", return_value=1000, create=True), patch("core.sandbox_runtime.os.getgid", return_value=1000, create=True):
                with self.assertRaisesRegex(SandboxRuntimeError, "mount syntax"):
                    build_docker_argv(SandboxPolicy(), image="python:3.12",
                        workspace=directory, command=["python", "--version"])

    def test_workspace_must_be_absolute_and_exist(self):
        with self.assertRaises(SandboxRuntimeError):
            build_docker_argv(SandboxPolicy(), image="python:3.12",
                workspace="relative/path", command=["python", "--version"])

    def test_launcher_options_are_validated_and_applied(self):
        with tempfile.TemporaryDirectory() as cid_dir:
            cidfile = str(__import__("pathlib").Path(cid_dir) / "cid")
            argv = build_docker_argv(
                SandboxPolicy(), image="python:3.12", workspace=self.tmp.name,
                command=["python", "--version"], docker_binary="/usr/bin/docker",
                runtime_user=(65534, 65534), cidfile=cidfile,
            )
            self.assertEqual(argv[0], "/usr/bin/docker")
            self.assertEqual(argv[argv.index("--user") + 1], "65534:65534")
            self.assertEqual(argv[argv.index("--cidfile") + 1], cidfile)

    def test_invalid_runtime_identity_and_cidfile_are_rejected(self):
        for runtime_user in ((0, 1000), (1000, 0), (True, 1000), [1000, 1000]):
            with self.subTest(runtime_user=runtime_user):
                with self.assertRaises(SandboxRuntimeError):
                    build_docker_argv(
                        SandboxPolicy(), image="python:3.12", workspace=self.tmp.name,
                        command=["python"], runtime_user=runtime_user,
                    )
        with self.assertRaises(SandboxRuntimeError):
            build_docker_argv(
                SandboxPolicy(), image="python:3.12", workspace=self.tmp.name,
                command=["python"], cidfile="relative/cid",
            )

    @patch("core.sandbox_runtime.os.getgid", return_value=1000, create=True)
    @patch("core.sandbox_runtime.os.getuid", return_value=0, create=True)
    def test_explicit_container_identity_does_not_bypass_host_root_refusal(self, _uid, _gid):
        with self.assertRaisesRegex(SandboxRuntimeError, "root service"):
            build_docker_argv(
                SandboxPolicy(), image="python:3.12", workspace=self.tmp.name,
                command=["python", "--version"], runtime_user=(65534, 65534),
            )


if __name__ == "__main__":
    unittest.main()
