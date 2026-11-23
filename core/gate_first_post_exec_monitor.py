"""Live bounded output and exit monitoring for gate-first Docker containers."""
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
    """Follow live output and verify exit for one exact daemon container ID."""

    def __init__(
        self, *, docker_client: DockerGateContainerClient,
        poll_interval_seconds: float = 0.1, output_limit_bytes: int = 65_536,
    ) -> None:
        if not isinstance(docker_client, DockerGateContainerClient):
            raise TypeError("docker_client must be a DockerGateContainerClient")
        if (
            isinstance(poll_interval_seconds, bool)
            or not isinstance(poll_interval_seconds, (int, float))
            or not 0.02 <= poll_interval_seconds <= 1
        ):
            raise ValueError("poll_interval_seconds must be between 0.02 and 1")
        if type(output_limit_bytes) is not int or not 1 <= output_limit_bytes <= 65_536:
            raise ValueError("output_limit_bytes must be between 1 and 65536")
        self.docker_client = docker_client
        self.poll_interval_seconds = float(poll_interval_seconds)
        self.output_limit_bytes = output_limit_bytes

    def __call__(
        self, *, container: CreatedGateContainer, snapshot: object, task: object,
        execution_spec: object, profile: SandboxExecutionProfile, started_at: float,
        cancellation_check: Callable[[], bool] | None = None,
    ) -> SandboxExecutionResult:
        if (
            not isinstance(container, CreatedGateContainer)
            or type(container.container_id) is not str
            or len(container.container_id) != 64
            or not isinstance(profile, SandboxExecutionProfile)
        ):
            raise ValueError("monitor requires the exact created container and immutable profile")
        if cancellation_check is not None and not callable(cancellation_check):
            raise TypeError("cancellation_check must be callable")
        deadline = started_at + profile.policy.timeout_seconds
        cancelled = False

        def should_stop() -> str | None:
            nonlocal cancelled
            if cancellation_check is not None:
                try:
                    cancelled = cancellation_check() is True
                except Exception:
                    # A failed lease/cancellation check is not permission to continue.
                    cancelled = True
                if cancelled:
                    return "cancelled"
            if time.monotonic() >= deadline:
                return "timeout"
            return None

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            stdout = stderr = b""
            output_truncated = False
        else:
            ceiling = min(self.output_limit_bytes, profile.policy.max_output_bytes)
            stdout, stderr, output_truncated, stop_reason = self.docker_client.follow_logs(
                container_id=container.container_id,
                should_stop=should_stop,
                timeout_seconds=max(0.1, remaining),
                output_limit_bytes=ceiling,
            )
            timed_out = stop_reason == "timeout"
            cancelled = cancelled or stop_reason == "cancelled"
            output_truncated = output_truncated or stop_reason == "output_limit"

        if timed_out or cancelled or output_truncated:
            self.docker_client.remove(container_name=container.name)

        exit_code = -1
        if not timed_out and not cancelled and not output_truncated:
            inspected = self.docker_client._run([
                self.docker_client.docker_binary, "inspect", container.container_id,
            ])
            if getattr(inspected, "returncode", None) != 0:
                raise ExecutionGateDockerLifecycleError(
                    "Docker could not inspect the completed task container"
                )
            raw = getattr(inspected, "stdout", b"")
            try:
                payload = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
                if type(payload) is not list or len(payload) != 1:
                    raise ValueError("invalid inspect payload")
                item = payload[0]
                state = item["State"]
                if (
                    not isinstance(item, dict)
                    or item.get("Id") != container.container_id
                    or not isinstance(state, dict)
                    or state.get("Running") is not False
                    or type(state.get("OOMKilled")) is not bool
                ):
                    raise ValueError("inspect identity or state mismatch")
                code = state.get("ExitCode")
                if type(code) is not int or isinstance(code, bool) or not 0 <= code <= 255:
                    raise ValueError("invalid exit code")
                if state["OOMKilled"] and code == 0:
                    raise ValueError("OOM-killed container reported success")
                exit_code = code
            except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError) as exc:
                raise ExecutionGateDockerLifecycleError(
                    "Docker returned malformed or mismatched post-exec state"
                ) from exc

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
