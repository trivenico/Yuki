from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from core.execution_gate_docker_lifecycle import CreatedGateContainer, DockerGateContainerClient
from core.execution_gate_session import ExecutionGateAuthorizationSession
from core.gate_first_post_exec_monitor import DockerGatePostExecMonitor
from core.gate_first_sandbox_executor import GateFirstDockerSandboxExecutor, GateFirstExecutionError
from core.sandbox_executor import SandboxExecutionProfile
from core.sandbox_policy import SandboxPolicy


def _docker_client(tmp_path, runner=None):
    binary = tmp_path / "docker"
    binary.write_text("#!/bin/sh\\nexit 0\\n", encoding="utf-8")
    binary.chmod(0o700)
    return DockerGateContainerClient(
        docker_binary=str(binary),
        docker_host="unix:///tmp/docker.sock",
        runner=runner or (lambda *args, **kwargs: None),
    )


def test_gate_first_executor_uses_builtin_post_exec_monitor(tmp_path):
    private_root = tmp_path / "private"
    private_root.mkdir(mode=0o700)
    private_root.chmod(0o700)
    client = _docker_client(tmp_path)
    executor = GateFirstDockerSandboxExecutor(
        docker_client=client,
        docker_binary=str(tmp_path / "docker"),
        apparmor_profile="docker-default",
        private_directory_root=str(private_root),
        expected_gate_host_uid=1000,
        uid_resolver=lambda pid: 1000,
        exec_observer=lambda *args, **kwargs: True,
        supervisor_collector_factory=lambda **kwargs: None,
    )
    assert isinstance(executor.post_exec_monitor, DockerGatePostExecMonitor)


def test_gate_first_executor_rejects_invalid_lease_before_docker(tmp_path):
    private_root = tmp_path / "private"
    private_root.mkdir(mode=0o700)
    private_root.chmod(0o700)
    executor = GateFirstDockerSandboxExecutor(
        docker_client=_docker_client(tmp_path),
        docker_binary=str(tmp_path / "docker"),
        apparmor_profile="docker-default",
        private_directory_root=str(private_root),
        expected_gate_host_uid=1000,
        uid_resolver=lambda pid: 1000,
        exec_observer=lambda *args, **kwargs: True,
        supervisor_collector_factory=lambda **kwargs: None,
    )
    with pytest.raises(GateFirstExecutionError, match="lease"):
        executor.execute(
            task=object(),
            worker_id="worker-1",
            execution_spec=None,
            profile=None,
        )
    assert list(private_root.iterdir()) == []


def test_post_exec_monitor_polls_exact_container_and_bounds_logs(tmp_path):
    calls = []
    def runner(argv, **kwargs):
        calls.append(argv)
        if "inspect" in argv:
            payload = [{"Id": "c" * 64, "State": {"Running": False, "ExitCode": 0, "OOMKilled": False}}]
            return SimpleNamespace(returncode=0, stdout=json.dumps(payload).encode(), stderr=b"")
        return SimpleNamespace(returncode=0, stdout=b"hello", stderr=b"error")

    client = _docker_client(tmp_path, runner)
    client.follow_logs = lambda **kwargs: (b"hello", b"err", False, None)
    monitor = DockerGatePostExecMonitor(docker_client=client, output_limit_bytes=8)
    policy = SandboxPolicy(max_output_bytes=8).validate()
    profile = SandboxExecutionProfile(
        spec_id="spec-1", image="python@sha256:" + "a" * 64,
        policy=policy, command=("python", "-V"), workspace_resolver=lambda task: "/tmp",
    )
    container = CreatedGateContainer("yuki-" + "b" * 32, "c" * 64)
    result = monitor(
        container=container, snapshot=object(), task=object(),
        execution_spec=object(), profile=profile, started_at=__import__("time").monotonic(),
    )
    assert result.container_id == "c" * 64
    assert result.exit_code == 0
    assert result.stdout == b"hello"
    assert result.stderr == b"err"
    assert result.output_truncated is False
    assert calls[0][-2:] == ["inspect", "c" * 64]


def test_post_exec_monitor_cancels_and_removes_container(tmp_path):
    calls = []
    def runner(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    client = _docker_client(tmp_path, runner)
    client.follow_logs = lambda **kwargs: (b"", b"", False, kwargs["should_stop"]())
    monitor = DockerGatePostExecMonitor(docker_client=client)
    profile = SandboxExecutionProfile(
        spec_id="spec-1", image="python@sha256:" + "a" * 64,
        policy=SandboxPolicy().validate(), command=("python", "-V"),
        workspace_resolver=lambda task: "/tmp",
    )
    container = CreatedGateContainer("yuki-" + "b" * 32, "c" * 64)
    result = monitor(
        container=container, snapshot=object(), task=object(),
        execution_spec=object(), profile=profile,
        started_at=__import__("time").monotonic(),
        cancellation_check=lambda: True,
    )
    assert result.cancelled is True
    assert result.exit_code == -1
    assert calls[0][-2:] == ["rm", "-f", container.name]


def test_post_exec_monitor_stops_and_removes_on_live_output_overflow(tmp_path):
    calls = []
    def runner(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    client = _docker_client(tmp_path, runner)
    client.follow_logs = lambda **kwargs: (b"x" * 8, b"", True, "output_limit")
    monitor = DockerGatePostExecMonitor(docker_client=client, output_limit_bytes=8)
    profile = SandboxExecutionProfile(
        spec_id="spec-1", image="python@sha256:" + "a" * 64,
        policy=SandboxPolicy(max_output_bytes=8).validate(),
        command=("python", "-V"), workspace_resolver=lambda task: "/tmp",
    )
    container = CreatedGateContainer("yuki-" + "b" * 32, "c" * 64)
    result = monitor(
        container=container, snapshot=object(), task=object(),
        execution_spec=object(), profile=profile,
        started_at=__import__("time").monotonic(),
    )
    assert result.output_truncated is True
    assert result.exit_code == -1
    assert calls[0][-2:] == ["rm", "-f", container.name]
