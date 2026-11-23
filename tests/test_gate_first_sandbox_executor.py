from __future__ import annotations

import os

import pytest

from core.execution_gate_docker_lifecycle import DockerGateContainerClient
from core.gate_first_sandbox_executor import GateFirstDockerSandboxExecutor, GateFirstExecutionError


def _docker_client(tmp_path):
    binary = tmp_path / "docker"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o700)
    return DockerGateContainerClient(
        docker_binary=str(binary),
        docker_host="unix:///tmp/docker.sock",
        runner=lambda *args, **kwargs: None,
    )


def test_gate_first_executor_requires_post_exec_monitor(tmp_path):
    private_root = tmp_path / "private"
    private_root.mkdir(mode=0o700)
    private_root.chmod(0o700)
    with pytest.raises(TypeError, match="post_exec_monitor"):
        GateFirstDockerSandboxExecutor(
            docker_client=_docker_client(tmp_path),
            docker_binary=str(tmp_path / "docker"),
            apparmor_profile="docker-default",
            private_directory_root=str(private_root),
            expected_gate_host_uid=1000,
            uid_resolver=lambda pid: 1000,
            exec_observer=lambda *args, **kwargs: True,
            supervisor_collector_factory=lambda **kwargs: None,
            post_exec_monitor=None,
        )


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
        post_exec_monitor=lambda **kwargs: None,
    )
    with pytest.raises(GateFirstExecutionError, match="lease"):
        executor.execute(
            task=object(),
            worker_id="worker-1",
            execution_spec=None,
            profile=None,
        )
    assert list(private_root.iterdir()) == []
