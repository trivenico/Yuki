"""Bounded post-exec monitoring for gate-first Docker containers.

The gate has already exec-replaced itself before this monitor is entered.
Monitoring uses Docker inspect polling and bounded post-exit log retrieval;
it never starts, execs, or restarts a workload.
"""
from __future__ import annotations

import json
import time
from typing import Callable

from core.execution_gate_docker_lifecycle import (
    CreatedGateContainer,
    DockerGateContainerClient,
    ExecutionGateDockerLifecycleError,
)
from core.sandbox_executor import SandboxExecutionProfile, SandboxExecutionResult


class DockerGatePostExecMonitor:
    """Poll one exact daemon container ID, then retrieve bounded task logs."""

    def __init__(
        self,
        *,
        docker_client: DockerGateContainerClient,
        poll_interval_seconds: float = 0.1,
        output_limit_bytes: int = 65_536,
    ) -> None:
        if not isinstance(docker_client, DockerGateContainerClient):
            raise TypeError("docker_client must be a DockerGateContainerClient")
        if isinstance(poll_interval_seconds, bool) or not isinstance(poll_interval_seconds, (int, float)) or not 0.02 <= poll_interval_seconds <= 1:
            raise ValueError("poll_interval_seconds must be between 0.02 and 1")
        if type(output_limit_bytes) is not int or not 1 <= output_limit_bytes <= 65_536:
            raise ValueError("output_limit_bytes must be between 1 and 65536")
        self.docker_client = docker_client
        self.poll_interval_seconds = float(poll_interval_seconds)
        self.output_limit_bytes = output_limit_bytes

    def __call__(
        self,
        *,
        container: CreatedGateContainer,
        snapshot: object,
        task: object,
        execution_spec: object,
        profile: SandboxExecutionProfile,
        started_at: float,
        cancellation_check: Callable[[], bool] | None = None,
    ) -> SandboxExecutionResult:
        if (
            not isinstance(container, CreatedGateContainer)
            or type(container.container_id) is not str
            or len(container.container_id) != 64
            or not isinstance(profile, SandboxExecutionProfile)
        ):
            raise ValueError("monitor requires the exact created container and immutable profile")
        deadline = started_at + profile.policy.timeout_seconds
        timed_out = False
        cancelled = False
        exit_code = -1
        while True:
            if cancellation_check is not None:
                try:
                    cancelled = cancellation_check() is True
                except Exception:
                    cancelled = True
                if cancelled:
                    self.docker_client.remove(container_name=container.name)
                    break
            if time.monotonic() >= deadline:
                timed_out = True
                self.docker_client.remove(container_name=container.name)
                break
            result = self.docker_client._run([
                self.docker_client.docker_binary, "inspect", container.container_id,
            ])
            if getattr(result, "returncode", None) != 0:
                raise ExecutionGateDockerLifecycleError(
                    "Docker could not inspect the monitored gate container"
                )
            raw = getattr(result, "stdout", b"")
            try:
                payload = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
                state = payload[0]["State"]
                if (
                    type(payload) is not list or len(payload) != 1
                    or not isinstance(state, dict)
                    or type(state.get("Running")) is not bool
                ):
                    raise ValueError("invalid inspect response")
            except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError) as exc:
                raise ExecutionGateDockerLifecycleError(
                    "Docker returned malformed post-exec process state"
                ) from exc
            if state["Running"] is False:
                code = state.get("ExitCode")
                if type(code) is not int or isinstance(code, bool) or not 0 <= code <= 255:
                    raise ExecutionGateDockerLifecycleError("Docker returned an invalid task exit code")
                exit_code = code
                break
            time.sleep(self.poll_interval_seconds)

        stdout = b""
        stderr = b""
        output_truncated = False
        if not timed_out and not cancelled:
            logs = self.docker_client._run([
                self.docker_client.docker_binary, "logs", container.container_id,
            ])
            if getattr(logs, "returncode", None) != 0:
                raise ExecutionGateDockerLifecycleError("Docker could not retrieve completed task logs")
            out = getattr(logs, "stdout", b"")
            err = getattr(logs, "stderr", b"")
            if isinstance(out, str):
                out = out.encode("utf-8", errors="replace")
            if isinstance(err, str):
                err = err.encode("utf-8", errors="replace")
            if not isinstance(out, bytes) or not isinstance(err, bytes):
                raise ExecutionGateDockerLifecycleError("Docker logs returned unsupported output types")
            output_truncated = len(out) + len(err) > min(
                self.output_limit_bytes, profile.policy.max_output_bytes,
            )
            ceiling = min(self.output_limit_bytes, profile.policy.max_output_bytes)
            stdout = out[:ceiling]
            stderr = err[:max(0, ceiling - len(stdout))]
        return SandboxExecutionResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=max(0.0, time.monotonic() - started_at),
            timed_out=timed_out,
            output_truncated=output_truncated,
            container_id=container.container_id,
            cancelled=cancelled,
        )
