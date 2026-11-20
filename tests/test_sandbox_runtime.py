import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.sandbox_policy import SandboxPolicy
from core.sandbox_runtime import (
    SandboxRuntimeError, SandboxUnavailable, SandboxRuntimeCapabilities,
    build_docker_argv, validate_runtime_capabilities,
)


IMAGE = "python@sha256:" + "a" * 64


class DockerSandboxArgvTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    @patch("core.sandbox_runtime.os.getgid", return_value=1000, create=True)
    @patch("core.sandbox_runtime.os.getuid", return_value=1000, create=True)
    def test_restrictive_flags_and_read_only_workspace_default(self, _uid, _gid):
        command = ["python", "--version"]
        argv = build_docker_argv(SandboxPolicy(), image=IMAGE,
            workspace=self.tmp.name, command=command)
        self.assertEqual(argv[argv.index("--network") + 1], "none")
        self.assertIn("--read-only", argv)
        self.assertIn("--cap-drop", argv)
        self.assertIn("ALL", argv)
        self.assertIn("--pids-limit", argv)
        self.assertIn("--pull=never", argv)
        self.assertEqual(argv[argv.index("--memory-swap") + 1], argv[argv.index("--memory") + 1])
        mount = argv[argv.index("--mount") + 1]
        self.assertTrue(mount.endswith(",ro"))
        self.assertEqual(argv[-2:], command)

    @patch("core.sandbox_runtime.os.getgid", return_value=1000, create=True)
    @patch("core.sandbox_runtime.os.getuid", return_value=1000, create=True)
    def test_write_workspace_capability_is_explicit(self, _uid, _gid):
        policy = SandboxPolicy(capabilities=frozenset({"write_workspace"}))
        argv = build_docker_argv(policy, image=IMAGE,
            workspace=self.tmp.name, command=["python", "--version"])
        self.assertTrue(argv[argv.index("--mount") + 1].endswith(",rw"))

    @patch("core.sandbox_runtime.os.getgid", return_value=1000, create=True)
    @patch("core.sandbox_runtime.os.getuid", return_value=1000, create=True)
    def test_allowlist_fails_closed_without_gateway(self, _uid, _gid):
        policy = SandboxPolicy(network_mode="allowlist",
            capabilities=frozenset({"network_allowlist"}))
        with self.assertRaises(SandboxRuntimeError):
            build_docker_argv(policy, image=IMAGE,
                workspace=self.tmp.name, command=["python", "--version"])

    @patch("core.sandbox_runtime.os.getgid", return_value=1000, create=True)
    @patch("core.sandbox_runtime.os.getuid", return_value=0, create=True)
    def test_root_service_is_rejected(self, _uid, _gid):
        with self.assertRaises(SandboxRuntimeError):
            build_docker_argv(SandboxPolicy(), image=IMAGE,
                workspace=self.tmp.name, command=["python", "--version"])

    @patch("core.sandbox_runtime.os.getgid", return_value=1000, create=True)
    @patch("core.sandbox_runtime.os.getuid", return_value=1000, create=True)
    def test_invalid_image_and_command_are_rejected(self, _uid, _gid):
        with self.assertRaises(SandboxRuntimeError):
            build_docker_argv(SandboxPolicy(), image="python; invalid",
                workspace=self.tmp.name, command=["python", "--version"])
        with self.assertRaises(SandboxRuntimeError):
            build_docker_argv(SandboxPolicy(), image=IMAGE,
                workspace=self.tmp.name, command="python --version")
        with self.assertRaises(SandboxRuntimeError):
            build_docker_argv(SandboxPolicy(), image=IMAGE,
                workspace=self.tmp.name, command=["python", "bad\x00arg"])

    def test_workspace_must_be_absolute_and_exist(self):
        with self.assertRaises(SandboxRuntimeError):
            build_docker_argv(SandboxPolicy(), image=IMAGE,
                workspace="relative/path", command=["python", "--version"])


    @patch("core.sandbox_runtime.os.getgid", return_value=1000, create=True)
    @patch("core.sandbox_runtime.os.getuid", return_value=1000, create=True)
    def test_mutable_image_tag_is_rejected(self, _uid, _gid):
        with self.assertRaises(SandboxRuntimeError):
            build_docker_argv(SandboxPolicy(), image="python:3.12",
                workspace=self.tmp.name, command=["python", "--version"])

    @patch("core.sandbox_runtime.os.getgid", return_value=1000, create=True)
    @patch("core.sandbox_runtime.os.getuid", return_value=1000, create=True)
    def test_symlink_workspace_is_rejected(self, _uid, _gid):
        link = Path(self.tmp.name).parent / (Path(self.tmp.name).name + "-link")
        try:
            link.symlink_to(self.tmp.name, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        self.addCleanup(lambda: link.unlink(missing_ok=True))
        with self.assertRaises(SandboxRuntimeError):
            build_docker_argv(SandboxPolicy(), image=IMAGE,
                workspace=str(link), command=["python", "--version"])

    @patch("core.sandbox_runtime.os.getgid", return_value=1000, create=True)
    @patch("core.sandbox_runtime.os.getuid", return_value=1000, create=True)
    def test_comma_in_workspace_path_is_rejected(self, _uid, _gid):
        with tempfile.TemporaryDirectory(prefix="yuki,unsafe-") as unsafe:
            with self.assertRaises(SandboxRuntimeError):
                build_docker_argv(SandboxPolicy(), image=IMAGE,
                    workspace=unsafe, command=["python", "--version"])


class SandboxRuntimeCapabilityTests(unittest.TestCase):
    def setUp(self):
        self.policy = SandboxPolicy().validate()
        self.capabilities = SandboxRuntimeCapabilities(
            runtime_name="rootless-oci", runtime_version="1.0",
            rootless=True,
            namespaces=frozenset({"user", "pid", "mount", "ipc", "network"}),
            seccomp=True, mandatory_access_control="apparmor",
            cgroups_v2=True, network_isolation=True, read_only_mounts=True,
            host_secret_exclusion=True, resource_limits=True,
            ephemeral_cleanup=True, verified_backend=True,
        )

    def test_all_required_capabilities_are_accepted(self):
        validate_runtime_capabilities(self.policy, self.capabilities)

    def test_unverified_backend_fails_closed(self):
        values = dict(self.capabilities.__dict__)
        values["verified_backend"] = False
        with self.assertRaises(SandboxUnavailable):
            validate_runtime_capabilities(
                self.policy, SandboxRuntimeCapabilities(**values)
            )

    def test_missing_isolation_control_fails_closed(self):
        values = dict(self.capabilities.__dict__)
        values["network_isolation"] = False
        values["host_secret_exclusion"] = False
        with self.assertRaises(SandboxUnavailable):
            validate_runtime_capabilities(
                self.policy, SandboxRuntimeCapabilities(**values)
            )

    def test_mutable_namespace_collection_is_rejected(self):
        values = dict(self.capabilities.__dict__)
        values["namespaces"] = set(values["namespaces"])
        with self.assertRaises(SandboxRuntimeError):
            SandboxRuntimeCapabilities(**values)


if __name__ == "__main__":
    unittest.main()
