import io
import os
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.sandbox_policy import SandboxPolicy
from core.sandbox_executor import (
    DockerSandboxExecutor, SandboxRuntimeError, SandboxVerification,
)


CONTROLS = frozenset({
    "rootless", "user_namespace", "pid_namespace", "mount_namespace",
    "ipc_namespace", "network_namespace", "seccomp", "mandatory_access_control",
    "cgroups_v2", "network_isolation", "read_only_root", "workspace_mount_policy",
    "host_secret_exclusion", "cpu_limit", "memory_limit", "process_limit",
    "ephemeral_cleanup",
})
IMAGE = "python@sha256:" + "a" * 64


class FakeProcess:
    def __init__(self, *args, **kwargs):
        self.stdout = io.BytesIO(b"safe output")
        self.stderr = io.BytesIO(b"")
        self.returncode = 0
        self.pid = 12345

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode


class DockerSandboxExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.binary = self.root / "docker"
        self.binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.binary.chmod(0o700)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.docker_socket = self.root / "docker.sock"
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(self.docker_socket))
        listener.close()
        self.policy = SandboxPolicy().validate()
        self.holder = {}
        self.executor = DockerSandboxExecutor(
            docker_binary=str(self.binary), docker_host_socket=str(self.docker_socket),
            runtime_verifier=lambda policy, image: self.holder["evidence"],
        )
        self.holder["evidence"] = SandboxVerification(
            verifier_id="host-probe-v1", image=IMAGE,
            policy_sha256=self.executor._policy_digest(self.policy),
            controls=CONTROLS, expires_at=__import__("time").time() + 60,
            apparmor_profile="docker-default",
        )

    def test_requires_valid_operator_verification_before_launch(self):
        self.holder["evidence"] = SandboxVerification(
            verifier_id="untrusted", image=IMAGE,
            policy_sha256="0" * 64, controls=CONTROLS,
            expires_at=__import__("time").time() + 60,
        )
        with patch("core.sandbox_executor.subprocess.Popen") as launch:
            with self.assertRaisesRegex(SandboxRuntimeError, "verification evidence"):
                self.executor.execute(
                    policy=self.policy, image=IMAGE, workspace=str(self.workspace),
                    command=["python", "--version"],
                )
        launch.assert_not_called()

    @patch("core.sandbox_runtime.os.getgid", return_value=1000, create=True)
    @patch("core.sandbox_runtime.os.getuid", return_value=1000, create=True)
    def test_executes_with_unique_container_name_and_cleans_up(self, _uid, _gid):
        with patch("core.sandbox_executor.subprocess.Popen", side_effect=FakeProcess) as launch, patch(
            "core.sandbox_executor.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, b"", b""),
        ) as cleanup:
            result = self.executor.execute(
                policy=self.policy, image=IMAGE, workspace=str(self.workspace),
                command=["python", "--version"],
            )
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout, b"safe output")
        self.assertFalse(result.timed_out)
        self.assertTrue(result.container_id.startswith("yuki-"))
        argv = launch.call_args.args[0]
        self.assertEqual(argv[0], str(self.binary))
        self.assertEqual(launch.call_args.kwargs["env"]["DOCKER_HOST"], "unix://" + str(self.docker_socket))
        self.assertEqual(argv[2], "--name")
        self.assertEqual(argv[3], result.container_id)
        self.assertIn("seccomp=default", argv)
        self.assertIn("apparmor=docker-default", argv)
        cleanup.assert_called_once()
        self.assertEqual(cleanup.call_args.args[0][-2:], ["-f", result.container_id])

    @patch("core.sandbox_runtime.os.getgid", return_value=1000, create=True)
    @patch("core.sandbox_runtime.os.getuid", return_value=1000, create=True)
    def test_cleanup_failure_is_not_reported_as_success(self, _uid, _gid):
        with patch("core.sandbox_executor.subprocess.Popen", FakeProcess), patch(
            "core.sandbox_executor.subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, b"", b"daemon unavailable"),
        ):
            with self.assertRaisesRegex(SandboxRuntimeError, "cleanup could not be verified"):
                self.executor.execute(
                    policy=self.policy, image=IMAGE, workspace=str(self.workspace),
                    command=["python", "--version"],
                )

    def test_rejects_non_executable_or_symlink_docker_binary(self):
        with self.assertRaises(ValueError):
            DockerSandboxExecutor(
                docker_binary=str(self.root / "missing"), docker_host_socket=str(self.docker_socket),
                runtime_verifier=lambda *_: self.holder["evidence"],
            )
        link = self.root / "docker-link"
        link.symlink_to(self.binary)
        with self.assertRaises(ValueError):
            DockerSandboxExecutor(
                docker_binary=str(link), docker_host_socket=str(self.docker_socket),
                runtime_verifier=lambda *_: self.holder["evidence"],
            )


if __name__ == "__main__":
    unittest.main()
