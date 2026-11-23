from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from automation.agent_execution_spec import AgentExecutionSpec, ExecutionBudget
from core.execution_gate_docker_lifecycle import CreatedGateContainer, DockerGateContainerClient, ExecutionGateDockerLifecycleError
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



@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt])
@pytest.mark.parametrize("cleanup_verified", [True, False])
def test_executor_records_terminal_failure_when_post_exec_monitor_raises(
    tmp_path, monkeypatch, error_type, cleanup_verified,
):
    private_root = tmp_path / "private"
    private_root.mkdir(mode=0o700)
    private_root.chmod(0o700)
    client = _docker_client(tmp_path)
    container = CreatedGateContainer("yuki-" + "b" * 32, "c" * 64)
    client.create = lambda **kwargs: container
    client.verify_created_container = lambda **kwargs: None
    client.start = lambda created: None
    removed_ids = []
    timeline = []
    def remove_container(**kwargs):
        removed_ids.append(kwargs["container_id"])
        timeline.append("container_removed")
        return cleanup_verified
    client.remove_container = remove_container

    class FakeSession:
        private_gate_directory = "/private/gate"
        gate_arguments = ("--gate",)
        def __init__(self, **kwargs):
            pass
        def open(self):
            return None
        def close(self):
            return None
        def bind_gate_identity(self, *args, **kwargs):
            return None
        def release(self, *args, **kwargs):
            return None

    monkeypatch.setattr(
        "core.gate_first_sandbox_executor.build_gate_container_create_argv",
        lambda **kwargs: ("docker", "create", "gate-only"),
    )
    executor = GateFirstDockerSandboxExecutor(
        docker_client=client,
        docker_binary=str(tmp_path / "docker"),
        apparmor_profile="docker-default",
        private_directory_root=str(private_root),
        expected_gate_host_uid=1000,
        uid_resolver=lambda pid: 1000,
        exec_observer=lambda *args, **kwargs: True,
        supervisor_collector_factory=lambda **kwargs: (
            lambda *args: {"verified": True}
        ),
        session_factory=FakeSession,
        post_exec_monitor=lambda **kwargs: (_ for _ in ()).throw(error_type("monitor failed")),
    )
    spec = AgentExecutionSpec(
        spec_id="spec-1", role="reviewer", instructions="Run the approved task",
        capabilities=("sandbox.execute",),
        budget=ExecutionBudget(max_runtime_seconds=30, max_memory_mb=128),
    )
    profile = SandboxExecutionProfile(
        spec_id="spec-1", image="python@sha256:" + "a" * 64,
        policy=SandboxPolicy(timeout_seconds=30, memory_mb=128).validate(),
        command=("python", "-V"), workspace_resolver=lambda task: "/workspace",
    )
    task = SimpleNamespace(
        run_id="task-1", owner_id="owner-1", state="running",
        lease_owner="worker-1", agent_execution_spec_id="spec-1",
    )
    events = []
    expected_error = error_type if cleanup_verified else GateFirstExecutionError
    expected_message = "monitor failed" if cleanup_verified else "cleanup could not be verified"
    with pytest.raises(expected_error, match=expected_message):
        executor.execute(
            task=task, worker_id="worker-1", execution_spec=spec,
            profile=profile,
            lifecycle_callback=lambda kind, container_id, result: (
                timeline.append(kind), events.append((kind, container_id, result))
            ),
        )
    assert [event[0] for event in events] == (
        ["task.sandbox.started", "task.sandbox.failed"]
        if cleanup_verified else ["task.sandbox.started"]
    )
    assert events[0][1] == container.container_id
    if cleanup_verified:
        assert events[1][1] == container.container_id
        assert events[1][2] is None
    assert removed_ids == [container.container_id]
    assert timeline == (
        ["task.sandbox.started", "container_removed", "task.sandbox.failed"]
        if cleanup_verified else ["task.sandbox.started", "container_removed"]
    )
    assert list(private_root.iterdir()) == []

def test_post_exec_monitor_polls_exact_container_and_bounds_logs(tmp_path):
    calls = []
    def runner(argv, **kwargs):
        calls.append(argv)
        if "inspect" in argv:
            payload = [{"Id": "c" * 64, "State": {"Running": False, "Status": "exited", "ExitCode": 0, "OOMKilled": False}}]
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
    assert calls[0][-3:] == ["rm", "-f", container.container_id]


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
    assert calls[0][-3:] == ["rm", "-f", container.container_id]



def test_post_exec_monitor_fails_closed_when_cancelled_container_removal_is_unverified(tmp_path):
    client = _docker_client(tmp_path, lambda *args, **kwargs: SimpleNamespace(
        returncode=1, stdout=b"", stderr=b"permission denied",
    ))
    client.follow_logs = lambda **kwargs: (b"", b"", False, kwargs["should_stop"]())
    monitor = DockerGatePostExecMonitor(docker_client=client)
    profile = SandboxExecutionProfile(
        spec_id="spec-1", image="python@sha256:" + "a" * 64,
        policy=SandboxPolicy().validate(), command=("python", "-V"),
        workspace_resolver=lambda task: "/tmp",
    )
    container = CreatedGateContainer("yuki-" + "b" * 32, "c" * 64)
    with pytest.raises(Exception, match="state could not be reconciled"):
        monitor(
            container=container, snapshot=object(), task=object(),
            execution_spec=object(), profile=profile,
            started_at=__import__("time").monotonic(),
            cancellation_check=lambda: True,
        )
