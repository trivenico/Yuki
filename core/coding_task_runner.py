"""Approval-gated, bounded execution for coding tasks in Docker sandboxes.

This runner never invokes a host shell. It delegates isolation flags to
core.sandbox_runtime and refuses to start unless a trusted approval callback
returns exactly True. The caller wires that callback to Yuki's authenticated,
actor/session-bound approval coordinator.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import secrets
import subprocess
import threading
import time
from typing import Callable, Sequence

from core.sandbox_policy import SandboxPolicy, SandboxPolicyError
from core.sandbox_runtime import SandboxRuntimeError, build_docker_argv


class SandboxExecutionError(RuntimeError):
    """Execution was refused or the sandbox runtime could not be started."""


@dataclass(frozen=True)
class SandboxExecutionResult:
    returncode: int
    output: str
    timed_out: bool
    output_truncated: bool
    duration_seconds: float


def _docker_environment() -> dict[str, str]:
    """Pass only OS basics and Docker connection settings to the Docker CLI."""
    allowed = {
        "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE",
        "DOCKER_CONFIG", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY",
        "DOCKER_CERT_PATH",
    }
    return {key: value for key, value in os.environ.items() if key in allowed}


def _kill_container(container_name: str) -> None:
    """Best-effort bounded cleanup if the Docker client is killed while a task runs."""
    try:
        subprocess.run(
            ["docker", "kill", container_name],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            env=_docker_environment(),
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def run_sandboxed_task(
    policy: SandboxPolicy,
    *,
    image: str,
    workspace: str,
    command: Sequence[str],
    approve: Callable[[], bool],
) -> SandboxExecutionResult:
    """Run one approved command in a restrictive Docker container.

    The approval callback must adapt Yuki's trusted coordinator, not a
    model-generated boolean or a skill-controlled flag. It is called for every
    invocation. Commands are argv arrays (never shell strings); output is capped.
    """
    if not callable(approve):
        raise SandboxExecutionError("a trusted per-run approval callback is required")
    try:
        policy.validate()
        argv = build_docker_argv(
            policy, image=image, workspace=workspace, command=command
        )
    except (SandboxPolicyError, SandboxRuntimeError, TypeError, ValueError) as exc:
        raise SandboxExecutionError("sandbox request rejected by policy") from exc

    try:
        approved = approve()
    except Exception as exc:
        raise SandboxExecutionError("approval failed; execution denied") from exc
    if approved is not True:
        raise SandboxExecutionError("execution requires explicit approval")

    container_name = "yuki-task-" + secrets.token_hex(12)
    image_index = len(argv) - len(command) - 1
    argv[image_index:image_index] = ["--name", container_name]

    output = bytearray()
    output_truncated = threading.Event()
    reader_errors: list[BaseException] = []
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            shell=False,
            close_fds=True,
            env=_docker_environment(),
        )
    except OSError as exc:
        raise SandboxExecutionError("could not start Docker sandbox runtime") from exc

    def drain_output() -> None:
        try:
            stream = process.stdout
            if stream is None:
                return
            limit = policy.max_output_bytes
            while True:
                chunk = stream.read(min(8192, limit + 1))
                if not chunk:
                    break
                remaining = limit - len(output)
                if len(chunk) > remaining:
                    if remaining > 0:
                        output.extend(chunk[:remaining])
                    output_truncated.set()
                    try:
                        process.kill()
                    except OSError:
                        pass
                    break
                output.extend(chunk)
                if len(output) >= limit:
                    extra = stream.read(1)
                    if extra:
                        output_truncated.set()
                        try:
                            process.kill()
                        except OSError:
                            pass
                    break
        except BaseException as exc:
            reader_errors.append(exc)

    reader = threading.Thread(target=drain_output, name="yuki-sandbox-output", daemon=True)
    reader.start()
    timed_out = False
    try:
        returncode = process.wait(timeout=policy.timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            process.kill()
        except OSError:
            pass
        try:
            returncode = process.wait(timeout=5)
        except subprocess.TimeoutExpired as exc:
            _kill_container(container_name)
            raise SandboxExecutionError("sandbox runtime did not stop after timeout") from exc
    finally:
        reader.join(timeout=2)
        if reader.is_alive() and process.stdout is not None:
            try:
                process.stdout.close()
            except OSError:
                pass
            reader.join(timeout=1)

    if timed_out or output_truncated.is_set():
        _kill_container(container_name)
    if reader_errors:
        raise SandboxExecutionError("failed while collecting bounded sandbox output") from reader_errors[0]

    return SandboxExecutionResult(
        returncode=int(returncode),
        output=bytes(output).decode("utf-8", errors="replace"),
        timed_out=timed_out,
        output_truncated=output_truncated.is_set(),
        duration_seconds=max(0.0, time.monotonic() - started),
    )
