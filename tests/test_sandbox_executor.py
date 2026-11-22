import io
import os
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.sandbox_policy import SandboxPolicy
from core.sandbox_executor import (
    DockerSandboxExecutor, SandboxExecutionProfile, SandboxExecutionResult,
    SandboxRuntimeError, SandboxVerification,
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


class PendingProcess(FakeProcess):
    def poll(self):
        return None


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
            apparmor_profile="docker-default",
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
        self.assertNotIn("seccomp=unconfined", argv)
        self.assertIn("apparmor=docker-default", argv)
        cleanup.assert_called_once()
        self.assertEqual(cleanup.call_args.args[0][-2:], ["-f", result.container_id])

    @patch("core.sandbox_runtime.os.getgid", return_value=1000, create=True)
    @patch("core.sandbox_runtime.os.getuid", return_value=1000, create=True)
    @patch("core.sandbox_runtime.os.getgid", return_value=1000, create=True)
    @patch("core.sandbox_runtime.os.getuid", return_value=1000, create=True)
    def test_emits_start_and_completion_lifecycle_callbacks(self, _uid, _gid):
        events = []
        with patch("core.sandbox_executor.subprocess.Popen", side_effect=FakeProcess), patch(
            "core.sandbox_executor.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, b"", b""),
        ):
            result = self.executor.execute(
                policy=self.policy, image=IMAGE, workspace=str(self.workspace),
                command=["python", "--version"],
                lifecycle_callback=lambda kind, container_id, outcome: events.append(
                    (kind, container_id, outcome)
                ),
            )
        self.assertEqual([event[0] for event in events], [
            "task.sandbox.started", "task.sandbox.completed",
        ])
        self.assertEqual(events[0][1], result.container_id)
        self.assertIsNone(events[0][2])
        self.assertEqual(events[1][2], result)

    @patch("core.sandbox_runtime.os.getgid", return_value=1000, create=True)
    @patch("core.sandbox_runtime.os.getuid", return_value=1000, create=True)
    def test_adds_complete_yuki_ownership_labels_to_container(self, _uid, _gid):
        labels = {
            "yuki.managed_by": "yuki",
            "yuki.task_id": "task-123",
            "yuki.worker_id": "worker-123",
            "yuki.owner_sha256": "a" * 64,
            "yuki.profile_id": "python-review",
            "yuki.image_digest": IMAGE.rsplit("@", 1)[-1],
        }
        with patch(
            "core.sandbox_executor.subprocess.Popen", side_effect=FakeProcess
        ) as popen, patch(
            "core.sandbox_executor.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, b"", b""),
        ):
            self.executor.execute(
                policy=self.policy, image=IMAGE, workspace=str(self.workspace),
                command=["python", "--version"], container_labels=labels,
            )
        argv = popen.call_args.args[0]
        for key, value in labels.items():
            self.assertIn(f"{key}={value}", argv)
            self.assertIn("--label", argv)

    @patch("core.sandbox_runtime.os.getgid", return_value=1000, create=True)
    @patch("core.sandbox_runtime.os.getuid", return_value=1000, create=True)
    def test_enforces_policy_specific_output_ceiling(self, _uid, _gid):
        policy = SandboxPolicy(max_output_bytes=4).validate()
        self.holder["evidence"] = SandboxVerification(
            verifier_id="host-probe-v1", image=IMAGE,
            policy_sha256=self.executor._policy_digest(policy),
            controls=CONTROLS, expires_at=__import__("time").time() + 60,
            apparmor_profile="docker-default",
        )
        with patch("core.sandbox_executor.subprocess.Popen", side_effect=FakeProcess), patch(
            "core.sandbox_executor.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, b"", b""),
        ):
            result = self.executor.execute(
                policy=policy, image=IMAGE, workspace=str(self.workspace),
                command=["python", "--version"],
            )
        self.assertEqual(result.stdout, b"safe")
        self.assertTrue(result.output_truncated)

    @patch("core.sandbox_runtime.os.getgid", return_value=1000, create=True)
    @patch("core.sandbox_runtime.os.getuid", return_value=1000, create=True)
    def test_cancellation_stops_client_and_removes_container(self, _uid, _gid):
        with patch("core.sandbox_executor.subprocess.Popen", side_effect=PendingProcess), patch(
            "core.sandbox_executor.os.killpg"
        ) as killpg, patch(
            "core.sandbox_executor.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, b"", b""),
        ) as cleanup:
            result = self.executor.execute(
                policy=self.policy, image=IMAGE, workspace=str(self.workspace),
                command=["python", "--version"], cancellation_check=lambda: True,
            )
        self.assertTrue(result.cancelled)
        killpg.assert_called()
        cleanup.assert_called_once()
        self.assertEqual(cleanup.call_args.args[0][-2:], ["-f", result.container_id])

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

    def test_authorized_task_requires_spec_capability_and_current_lease(self):
        from automation.agent_execution_spec import AgentExecutionSpec, ExecutionBudget

        spec = AgentExecutionSpec(
            spec_id="sandbox-review", role="review",
            instructions="Use the approved isolated command.",
            capabilities=("sandbox.execute",), budget=ExecutionBudget(
                max_runtime_seconds=5, max_memory_mb=128,
            ),
        )
        policy = SandboxPolicy(timeout_seconds=5, memory_mb=128)
        profile = SandboxExecutionProfile(
            spec_id=spec.spec_id, image=IMAGE, policy=policy,
            command=("/opt/yuki/check", "--safe"),
            workspace_resolver=lambda task: str(self.workspace),
        )
        task = SimpleNamespace(
            task_id="task-123", owner_id="alice", state="running", lease_owner="worker-1",
            agent_execution_spec_id=spec.spec_id,
        )
        expected = SandboxExecutionResult(0, b"ok", b"", 0.1, False, False, "yuki-test")
        with patch.object(self.executor, "execute", return_value=expected) as execute:
            result = self.executor.execute_authorized_task(
                task=task, execution_spec=spec, profile=profile,
                actor_id="worker-service", worker_id="worker-1",
                authorize=lambda actor, action, task_id: (
                    actor == "worker-service" and action == "task:sandbox:execute"
                    and task_id == "task-123"
                ),
            )
        self.assertIs(result, expected)
        execute.assert_called_once_with(
            policy=policy, image=IMAGE, workspace=str(self.workspace),
            command=profile.command,
        )

    def test_authorized_task_rejects_unauthorized_actor_before_workspace_resolution(self):
        from automation.agent_execution_spec import AgentExecutionSpec, ExecutionBudget

        spec = AgentExecutionSpec(
            spec_id="sandbox-review-denied", role="review",
            instructions="Use isolated command.", capabilities=("sandbox.execute",),
            budget=ExecutionBudget(max_runtime_seconds=5, max_memory_mb=128),
        )
        resolver_calls = []
        profile = SandboxExecutionProfile(
            spec_id=spec.spec_id, image=IMAGE,
            policy=SandboxPolicy(timeout_seconds=5, memory_mb=128),
            command=("/opt/yuki/check",),
            workspace_resolver=lambda task: resolver_calls.append(task) or str(self.workspace),
        )
        task = SimpleNamespace(
            task_id="task-denied", owner_id="alice", state="running", lease_owner="worker-1",
            agent_execution_spec_id=spec.spec_id,
        )
        with patch.object(self.executor, "execute") as execute:
            with self.assertRaises(PermissionError):
                self.executor.execute_authorized_task(
                    task=task, execution_spec=spec, profile=profile,
                    actor_id="untrusted", worker_id="worker-1",
                    authorize=lambda *_: False,
                )
        self.assertEqual(resolver_calls, [])
        execute.assert_not_called()

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


    def test_authorized_task_rejects_legacy_unowned_record(self):
        from automation.agent_execution_spec import AgentExecutionSpec, ExecutionBudget

        spec = AgentExecutionSpec(
            spec_id="sandbox-owner-check", role="review",
            instructions="Run the approved isolated command.",
            capabilities=("sandbox.execute",), budget=ExecutionBudget(
                max_runtime_seconds=5, max_memory_mb=128,
            ),
        )
        task = SimpleNamespace(
            task_id="legacy-task", state="running", lease_owner="worker-1",
            agent_execution_spec_id=spec.spec_id,
        )
        profile = SandboxExecutionProfile(
            spec_id=spec.spec_id, image=IMAGE,
            policy=SandboxPolicy(timeout_seconds=5, memory_mb=128),
            command=("python", "--version"),
            workspace_resolver=lambda _task: str(self.workspace),
        )
        with self.assertRaisesRegex(SandboxRuntimeError, "task lease or registered"):
            self.executor.execute_authorized_task(
                task=task, execution_spec=spec, profile=profile,
                actor_id="worker-1", worker_id="worker-1",
                authorize=lambda *_: True,
            )


if __name__ == "__main__":
    unittest.main()
