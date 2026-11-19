import tempfile
from pathlib import Path
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
        self.assertIn("--cap-drop", argv)
        self.assertIn("ALL", argv)
        self.assertIn("--pids-limit", argv)
        mount = argv[argv.index("--mount") + 1]
        self.assertTrue(mount.endswith(",ro"))
        self.assertEqual(argv[-2:], command)

    @patch("core.sandbox_runtime.os.getgid", return_value=1000, create=True)
    @patch("core.sandbox_runtime.os.getuid", return_value=1000, create=True)
    def test_trusted_workspace_write_capability_is_rejected(self, _uid, _gid):
        policy = SandboxPolicy(capabilities=frozenset({"write_workspace"}))
        with self.assertRaises(SandboxRuntimeError):
            build_docker_argv(policy, image="python:3.12",
                workspace=self.tmp.name, command=["python", "--version"])

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

    def test_workspace_must_be_absolute_and_exist(self):
        with self.assertRaises(SandboxRuntimeError):
            build_docker_argv(SandboxPolicy(), image="python:3.12",
                workspace="relative/path", command=["python", "--version"])



    @patch("core.sandbox_runtime.os.getgid", return_value=1000, create=True)
    @patch("core.sandbox_runtime.os.getuid", return_value=1000, create=True)
    def test_workspace_mount_path_with_comma_is_rejected(self, _uid, _gid):
        import os
        comma_root = Path(self.tmp.name) / "comma,workspace"
        comma_root.mkdir()
        with self.assertRaises(SandboxRuntimeError):
            build_docker_argv(
                SandboxPolicy(), image="python:3.12", workspace=str(comma_root),
                command=["python", "--version"],
            )

    @patch("core.sandbox_runtime.os.getgid", return_value=1000, create=True)
    @patch("core.sandbox_runtime.os.getuid", return_value=1000, create=True)
    def test_artifact_mount_path_with_comma_is_rejected(self, _uid, _gid):
        import os
        output = Path(self.tmp.name) / "comma,output"
        output.mkdir(mode=0o700)
        os.chmod(output, 0o700)
        with self.assertRaises(SandboxRuntimeError):
            build_docker_argv(
                SandboxPolicy(), image="python:3.12", workspace=self.tmp.name,
                command=["python", "--version"], artifact_output_directory=str(output),
            )

    @patch("core.sandbox_runtime.os.getgid", return_value=1000, create=True)
    @patch("core.sandbox_runtime.os.getuid", return_value=1000, create=True)
    def test_artifact_output_is_separate_private_writable_mount(self, _uid, _gid):
        import os
        output = tempfile.TemporaryDirectory()
        self.addCleanup(output.cleanup)
        os.chmod(output.name, 0o700)
        argv = build_docker_argv(
            SandboxPolicy(), image="python:3.12", workspace=self.tmp.name,
            command=["python", "-c", "print('ok')"],
            artifact_output_directory=output.name,
        )
        mounts = [argv[i + 1] for i, value in enumerate(argv[:-1]) if value == "--mount"]
        self.assertEqual(len(mounts), 2)
        self.assertTrue(mounts[0].endswith(",ro"))
        self.assertTrue(mounts[1].endswith(",rw"))
        self.assertIn("dst=/output,rw", mounts[1])
        self.assertEqual(argv[argv.index("--user") + 1], "1000:1000")

    @patch("core.sandbox_runtime.os.getgid", return_value=1000, create=True)
    @patch("core.sandbox_runtime.os.getuid", return_value=1000, create=True)
    def test_artifact_output_rejects_non_private_or_workspace_directory(self, _uid, _gid):
        import os
        from core.sandbox_runtime import SandboxRuntimeError
        output = tempfile.TemporaryDirectory()
        self.addCleanup(output.cleanup)
        os.chmod(output.name, 0o755)
        with self.assertRaises(SandboxRuntimeError):
            build_docker_argv(
                SandboxPolicy(), image="python:3.12", workspace=self.tmp.name,
                command=["python", "--version"], artifact_output_directory=output.name,
            )
        os.chmod(output.name, 0o700)
        with self.assertRaises(SandboxRuntimeError):
            build_docker_argv(
                SandboxPolicy(), image="python:3.12", workspace=self.tmp.name,
                command=["python", "--version"], artifact_output_directory=self.tmp.name,
            )

if __name__ == "__main__":
    unittest.main()
