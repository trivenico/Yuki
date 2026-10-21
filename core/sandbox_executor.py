"""Bounded Docker sandbox execution with timeout and container cleanup.

The argv builder defines container restrictions; this module is the execution
boundary. It never invokes a shell and returns only bounded output to callers.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import secrets
import signal
import subprocess
import threading
from typing import Sequence

from core.sandbox_policy import SandboxPolicy, SandboxPolicyError
from core.sandbox_runtime import SandboxRuntimeError, build_docker_argv
from core.container_recovery import container_name_for_execution


class SandboxExecutionError(RuntimeError):
    """Sandbox could not be started or safely completed."""


@dataclass(frozen=True)
class SandboxResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    output_truncated: bool
    container_cleanup_attempted: bool


def _bounded_reader(stream, limit: int, result: bytearray, overflow: list[bool]) -> None:
    try:
        while True:
            chunk = stream.read(8192)
            if not chunk:
                break
            remaining = limit - len(result)
            if remaining > 0:
                result.extend(chunk[:remaining])
            if len(chunk) > max(remaining, 0):
                overflow[0] = True
    finally:
        stream.close()


def execute_docker_sandbox(
    policy: SandboxPolicy,
    *,
    image: str,
    workspace: str,
    command: Sequence[str],
    execution_id: str | None = None,
) -> SandboxResult:
    """Run one isolated container, enforcing a wall-clock timeout and output cap.

    Container cleanup is attempted even when the client times out or raises.
    A cleanup failure raises SandboxExecutionError rather than claiming isolation
    completed successfully.
    """
    try:
        policy.validate()
        argv = build_docker_argv(policy, image=image, workspace=workspace, command=command)
    except (SandboxPolicyError, SandboxRuntimeError, AttributeError) as exc:
        raise SandboxExecutionError("Sandbox policy or configuration rejected.") from exc

    container_name = container_name_for_execution(execution_id) if execution_id is not None else "yuki-job-" + secrets.token_hex(12)
    # Use a named container and explicit cleanup instead of relying on --rm,
    # which cannot clean up if the Docker client itself is terminated.
    argv = [arg for arg in argv if arg != "--rm"]
    image_index = argv.index(image)
    identity_args = ["--name", container_name]
    if execution_id is not None:
        identity_args.extend(["--label", "com.yuki.managed=true", "--label", f"com.yuki.execution-id={execution_id}"])
    argv[image_index:image_index] = identity_args

    stdout = bytearray()
    stderr = bytearray()
    stdout_overflow = [False]
    stderr_overflow = [False]
    process = None
    timed_out = False
    cleanup_attempted = False
    cleanup_error = None
    returncode = -1
    limit = policy.max_output_bytes

    try:
        popen_kwargs = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "shell": False,
            "close_fds": True,
        }
        if os.name == "posix":
            popen_kwargs["start_new_session"] = True
        elif os.name == "nt" and hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

        process = subprocess.Popen(argv, **popen_kwargs)
        out_thread = threading.Thread(
            target=_bounded_reader, args=(process.stdout, limit, stdout, stdout_overflow), daemon=True
        )
        err_thread = threading.Thread(
            target=_bounded_reader, args=(process.stderr, limit, stderr, stderr_overflow), daemon=True
        )
        out_thread.start()
        err_thread.start()
        try:
            returncode = process.wait(timeout=policy.timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except (ProcessLookupError, OSError):
                try:
                    process.kill()
                except OSError:
                    pass
            process.wait()
            returncode = process.returncode
        out_thread.join(timeout=5)
        err_thread.join(timeout=5)
        if out_thread.is_alive() or err_thread.is_alive():
            raise SandboxExecutionError("Sandbox output streams did not close after process termination.")
    except SandboxExecutionError:
        raise
    except (OSError, ValueError) as exc:
        raise SandboxExecutionError("Sandbox process could not be started or monitored.") from exc
    finally:
        if process is not None and process.poll() is None:
            try:
                process.kill()
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                pass
        cleanup_attempted = True
        try:
            cleanup = subprocess.run(
                ["docker", "rm", "-f", container_name],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                timeout=5,
                check=False,
                close_fds=True,
            )
            if cleanup.returncode != 0:
                cleanup_error = "container cleanup returned a non-zero exit status"
        except (OSError, subprocess.TimeoutExpired):
            cleanup_error = "container cleanup could not be confirmed"

    if cleanup_error:
        raise SandboxExecutionError(
            "Sandbox container cleanup could not be confirmed; manual runtime inspection is required."
        )
    return SandboxResult(
        returncode=returncode,
        stdout=stdout.decode("utf-8", errors="replace"),
        stderr=stderr.decode("utf-8", errors="replace"),
        timed_out=timed_out,
        output_truncated=stdout_overflow[0] or stderr_overflow[0],
        container_cleanup_attempted=cleanup_attempted,
    )
