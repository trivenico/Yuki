"""Live bounded output and exit monitoring for gate-first Docker containers."""
from __future__ import annotations

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
            try:
                stdout, stderr, output_truncated, stop_reason = self.docker_client.follow_logs(
                    container_id=container.container_id,
                    should_stop=should_stop,
                    timeout_seconds=max(0.1, remaining),
                    output_limit_bytes=ceiling,
                )
            except ExecutionGateDockerLifecycleError as log_error:
                if "log transport failed; container state is unknown" not in str(log_error):
                    raise
                # A broken log stream does not prove exit. Ask the daemon for
                # the exact container state; never treat missing output as success.
                state = self.docker_client.inspect_container(container_id=container.container_id)
                if state["running"]:
                    self.docker_client.remove(container_name=container.name)
                    raise ExecutionGateDockerLifecycleError(
                        "Docker log transport failed and inspect confirmed the task container was still running; container termination was requested"
                    ) from log_error
                raise ExecutionGateDockerLifecycleError(
                    "Docker log transport failed; inspect confirmed container status "
                    + str(state["status"]) + " with exit code " + str(state["exit_code"])
                    + "; incomplete output cannot be reported as success"
                ) from log_error
            timed_out = stop_reason == "timeout"
            cancelled = cancelled or stop_reason == "cancelled"
            output_truncated = output_truncated or stop_reason == "output_limit"

        if timed_out or cancelled or output_truncated:
            self.docker_client.remove(container_name=container.name)

        exit_code = -1
        if not timed_out and not cancelled and not output_truncated:
            state = self.docker_client.inspect_container(container_id=container.container_id)
            if state["running"] is not False:
                raise ExecutionGateDockerLifecycleError(
                    "Docker task container is still running after log stream completion"
                )
            exit_code = state["exit_code"]

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
