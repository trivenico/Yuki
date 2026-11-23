"""Bounded Docker CLI control-plane operations for gate-first containers.

This client creates only the fixed gate command produced by
execution_gate_docker.build_gate_container_create_argv. It never accepts or
launches a task command and does not replace DockerSandboxExecutor.
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
from dataclasses import dataclass
from typing import Callable, Sequence


_CONTAINER_ID = re.compile(r"^[0-9a-f]{64}$")
_CONTAINER_NAME = re.compile(r"^yuki-[0-9a-f]{32}$")


class ExecutionGateDockerLifecycleError(RuntimeError):
    """Docker gate-container lifecycle failed closed."""


class DockerGateContainerNotFound(ExecutionGateDockerLifecycleError):
    """The daemon explicitly confirmed that an exact container ID is absent."""


@dataclass(frozen=True)
class CreatedGateContainer:
    name: str
    container_id: str


class DockerGateContainerClient:
    """Create, start and remove a gate container using bounded Docker CLI calls."""

    def __init__(
        self, *, docker_binary: str, docker_host: str,
        command_timeout_seconds: float = 10.0,
        runner: Callable[..., object] | None = None,
    ) -> None:
        if (
            type(docker_binary) is not str or not os.path.isabs(docker_binary)
            or os.path.islink(docker_binary) or not os.path.isfile(docker_binary)
            or not os.access(docker_binary, os.X_OK)
        ):
            raise ValueError("docker_binary must be an absolute non-symlink executable")
        if type(docker_host) is not str or not docker_host.startswith("unix:///"):
            raise ValueError("docker_host must be a local Unix-socket Docker endpoint")
        if (
            isinstance(command_timeout_seconds, bool)
            or type(command_timeout_seconds) not in (int, float)
            or not 0.1 <= command_timeout_seconds <= 30
        ):
            raise ValueError("command_timeout_seconds must be between 0.1 and 30")
        if runner is not None and not callable(runner):
            raise TypeError("runner must be callable when supplied")
        self.docker_binary = os.path.realpath(docker_binary)
        self.docker_host = docker_host
        self.command_timeout_seconds = float(command_timeout_seconds)
        self.runner = runner or _bounded_subprocess_run

    def _run(self, argv: Sequence[str]):
        try:
            result = self.runner(
                list(argv), stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                close_fds=True, timeout=self.command_timeout_seconds,
                check=False,
                env={
                    "PATH": os.path.dirname(self.docker_binary),
                    "LANG": "C.UTF-8", "DOCKER_HOST": self.docker_host,
                },
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ExecutionGateDockerLifecycleError(
                "Docker gate lifecycle command could not complete within its bound"
            ) from exc
        return result

    @staticmethod
    def _diagnostic(result: object) -> str:
        raw = getattr(result, "stderr", b"")
        if isinstance(raw, bytes):
            return raw[:2048].decode("utf-8", errors="replace").lower()
        if isinstance(raw, str):
            return raw[:2048].lower()
        return ""

    def _require_removed_after_uncertain_operation(
        self, *, container_name: str, operation: str, cause: BaseException,
    ) -> None:
        """Require daemon-confirmed cleanup after an ambiguous create/start."""
        try:
            removed = self.remove(container_name=container_name)
        except BaseException as cleanup_error:
            raise ExecutionGateDockerLifecycleError(
                f"Docker {operation} outcome is uncertain and cleanup could not be verified"
            ) from cleanup_error
        if not removed:
            raise ExecutionGateDockerLifecycleError(
                f"Docker {operation} outcome is uncertain and cleanup could not be verified"
            ) from cause

    def create(self, *, argv: Sequence[str], container_name: str) -> CreatedGateContainer:
        if type(container_name) is not str or not _CONTAINER_NAME.fullmatch(container_name):
            raise ValueError("container_name must be a generated Yuki container name")
        if (
            type(argv) not in (tuple, list) or len(argv) < 4
            or argv[0] != self.docker_binary or argv[1] != "create"
            or any(type(item) is not str or not item or "\x00" in item for item in argv)
            or "--entrypoint" not in argv
            or argv[argv.index("--entrypoint") + 1] != "/usr/local/bin/yuki-execution-gate"
            or "--name" not in argv
            or argv[argv.index("--name") + 1] != container_name
        ):
            raise ValueError("create argv must be the validated fixed-entrypoint Yuki gate command")
        try:
            result = self._run(argv)
        except BaseException as operation_error:
            # The daemon may have accepted create before timeout, interruption,
            # or transport loss. Never hide uncertain cleanup.
            self._require_removed_after_uncertain_operation(
                container_name=container_name, operation="create", cause=operation_error,
            )
            raise
        if getattr(result, "returncode", None) != 0:
            operation_error = ExecutionGateDockerLifecycleError(
                "Docker refused to create the gate container: " + self._diagnostic(result)
            )
            self._require_removed_after_uncertain_operation(
                container_name=container_name, operation="create", cause=operation_error,
            )
            raise operation_error
        raw = getattr(result, "stdout", b"")
        try:
            if isinstance(raw, bytes) and len(raw) <= 128:
                output = raw.decode("ascii", errors="strict").strip()
            elif isinstance(raw, str) and len(raw) <= 128:
                raw.encode("ascii", errors="strict")
                output = raw.strip()
            else:
                output = ""
        except UnicodeError:
            output = ""
        if not _CONTAINER_ID.fullmatch(output):
            operation_error = ExecutionGateDockerLifecycleError(
                "Docker create did not return one full daemon-assigned container ID"
            )
            self._require_removed_after_uncertain_operation(
                container_name=container_name, operation="create", cause=operation_error,
            )
            raise operation_error
        return CreatedGateContainer(name=container_name, container_id=output)

    def start(self, container: CreatedGateContainer) -> None:
        if (
            not isinstance(container, CreatedGateContainer)
            or not _CONTAINER_NAME.fullmatch(container.name)
            or not _CONTAINER_ID.fullmatch(container.container_id)
        ):
            raise ValueError("a validated daemon-assigned gate container identity is required")
        try:
            result = self._run([self.docker_binary, "start", container.container_id])
        except BaseException as operation_error:
            # Start may have succeeded before timeout, interruption, or loss
            # of the daemon connection. Reconcile by the unique Yuki name.
            self._require_removed_after_uncertain_operation(
                container_name=container.name, operation="start", cause=operation_error,
            )
            raise
        if getattr(result, "returncode", None) != 0:
            operation_error = ExecutionGateDockerLifecycleError(
                "Docker failed to start the waiting gate: " + self._diagnostic(result)
            )
            self._require_removed_after_uncertain_operation(
                container_name=container.name, operation="start", cause=operation_error,
            )
            raise operation_error

    def inspect_container(self, *, container_id: str) -> dict[str, object]:
        """Return daemon-authoritative state for one exact container identity."""
        if not _CONTAINER_ID.fullmatch(container_id):
            raise ValueError("a full daemon-assigned container ID is required")
        result = self._run([
            self.docker_binary, "inspect", "--type", "container", container_id,
        ])
        if getattr(result, "returncode", None) != 0:
            diagnostic = self._diagnostic(result)
            if getattr(result, "returncode", None) == 1 and container_id in diagnostic and ("no such object:" in diagnostic or "no such container:" in diagnostic):
                raise DockerGateContainerNotFound("Docker daemon confirmed the exact container ID is absent")
            raise ExecutionGateDockerLifecycleError("Docker could not establish container state: " + diagnostic)
        raw = getattr(result, "stdout", b"")
        try:
            import json
            document = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            payload = json.loads(document)
            if type(payload) is not list or len(payload) != 1 or not isinstance(payload[0], dict):
                raise ValueError("invalid inspect response")
            item = payload[0]
            state = item.get("State")
            if item.get("Id") != container_id or not isinstance(state, dict):
                raise ValueError("container identity mismatch")
            running = state.get("Running")
            status = state.get("Status")
            exit_code = state.get("ExitCode")
            oom_killed = state.get("OOMKilled")
            if (
                type(running) is not bool
                or status not in {"created", "running", "paused", "restarting", "removing", "exited", "dead"}
                or type(exit_code) is not int or not 0 <= exit_code <= 255
                or type(oom_killed) is not bool
                or (running and status in {"exited", "dead", "removing"})
                or (not running and status in {"running", "paused", "restarting"})
                or (oom_killed and exit_code == 0)
            ):
                raise ValueError("invalid container state")
            return {
                "container_id": container_id, "running": running,
                "status": status, "exit_code": exit_code, "oom_killed": oom_killed,
            }
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError, KeyError) as exc:
            raise ExecutionGateDockerLifecycleError(
                "Docker returned malformed or mismatched container state"
            ) from exc

    def follow_logs(
        self, *, container_id: str, should_stop: Callable[[], str | None],
        timeout_seconds: float, output_limit_bytes: int,
    ) -> tuple[bytes, bytes, bool, str | None]:
        """Follow bounded live output without background drain threads."""
        if not _CONTAINER_ID.fullmatch(container_id):
            raise ValueError("a full daemon-assigned container ID is required")
        if not callable(should_stop):
            raise TypeError("should_stop must be callable")
        if (
            isinstance(timeout_seconds, bool) or type(timeout_seconds) not in (int, float)
            or not 0.1 <= timeout_seconds <= 3600
            or type(output_limit_bytes) is not int or not 1 <= output_limit_bytes <= 65_536
        ):
            raise ValueError("invalid log-follow bounds")
        import selectors
        import time

        process = subprocess.Popen(
            [self.docker_binary, "logs", "--follow", container_id],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            close_fds=True, env={
                "PATH": os.path.dirname(self.docker_binary),
                "LANG": "C.UTF-8", "DOCKER_HOST": self.docker_host,
            }, start_new_session=True,
        )
        output = {"stdout": bytearray(), "stderr": bytearray()}
        total = 0
        truncated = False
        stop_reason = None
        selector = selectors.DefaultSelector()
        streams = (("stdout", process.stdout), ("stderr", process.stderr))
        started = time.monotonic()

        def signal_group(sig):
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                pass

        def terminate_group():
            signal_group(signal.SIGTERM)
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                pass
            signal_group(signal.SIGKILL)
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired as exc:
                raise ExecutionGateDockerLifecycleError(
                    "Docker live log follower process could not be reaped"
                ) from exc

        try:
            for key, pipe in streams:
                if pipe is not None:
                    os.set_blocking(pipe.fileno(), False)
                    selector.register(pipe, selectors.EVENT_READ, key)
            while selector.get_map():
                stop_reason = should_stop()
                if stop_reason is not None:
                    break
                if time.monotonic() - started >= timeout_seconds:
                    stop_reason = "timeout"
                    break
                for key, _ in selector.select(timeout=0.05):
                    try:
                        chunk = os.read(key.fd, 8192)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    remaining = max(0, output_limit_bytes - total)
                    keep = min(len(chunk), remaining)
                    if keep:
                        output[key.data].extend(chunk[:keep])
                        total += keep
                    if keep < len(chunk):
                        truncated = True
                        stop_reason = "output_limit"
                        break
                if stop_reason is not None:
                    break
            if stop_reason is not None:
                terminate_group()
            else:
                process.wait(timeout=1.0)
        finally:
            if process.poll() is None:
                terminate_group()
            elif selector.get_map():
                signal_group(signal.SIGKILL)
            for registered in list(selector.get_map().values()):
                try:
                    selector.unregister(registered.fileobj)
                except (KeyError, ValueError):
                    pass
            selector.close()
            for _, pipe in streams:
                if pipe is not None:
                    pipe.close()
        if stop_reason is None and process.returncode != 0:
            diagnostic = bytes(output["stderr"])[:2048].decode("utf-8", errors="replace").lower()
            # A failed logs transport is not evidence that the container exited.
            # Keep that distinction explicit so callers can reconcile container
            # state through the daemon instead of treating stream EOF as exit.
            detail = diagnostic if diagnostic else "no daemon diagnostic was returned"
            raise ExecutionGateDockerLifecycleError(
                "Docker live log transport failed; container state is unknown: " + detail
            )
        return bytes(output["stdout"]), bytes(output["stderr"]), truncated, stop_reason

    def remove_container(self, *, container_id: str) -> bool:
        """Remove by daemon ID; reconcile uncertain outcomes with bounded retries."""
        if type(container_id) is not str or not _CONTAINER_ID.fullmatch(container_id):
            raise ValueError("a full daemon-assigned container ID is required")
        command = [self.docker_binary, "rm", "-f", container_id]
        last_error = None
        for attempt in range(2):
            try:
                result = self._run(command)
                if getattr(result, "returncode", None) == 0:
                    return True
                last_error = ExecutionGateDockerLifecycleError("Docker refused container removal: " + self._diagnostic(result))
            except ExecutionGateDockerLifecycleError as exc:
                last_error = exc
            try:
                self.inspect_container(container_id=container_id)
            except DockerGateContainerNotFound:
                return True
            except ExecutionGateDockerLifecycleError as inspect_error:
                raise ExecutionGateDockerLifecycleError("Docker container removal is unverified because daemon state could not be reconciled") from inspect_error
            if attempt == 1:
                raise ExecutionGateDockerLifecycleError("Docker container remains after bounded removal attempts") from last_error
        raise ExecutionGateDockerLifecycleError("Docker container removal is unverified") from last_error

    def remove(self, *, container_name: str) -> bool:
        if type(container_name) is not str or not _CONTAINER_NAME.fullmatch(container_name):
            raise ValueError("container_name must be a generated Yuki container name")
        try:
            result = self._run([self.docker_binary, "rm", "-f", container_name])
        except ExecutionGateDockerLifecycleError:
            return False
        if getattr(result, "returncode", None) == 0:
            return True
        diagnostic = self._diagnostic(result)
        return (
            getattr(result, "returncode", None) == 1
            and "no such container" in diagnostic
        )


def _bounded_subprocess_run(
    argv, *, stdin, stdout, stderr, close_fds, timeout, check, env,
):
    """Run Docker CLI without unbounded stdout/stderr accumulation."""
    import threading

    process = subprocess.Popen(
        argv, stdin=stdin, stdout=stdout, stderr=stderr,
        close_fds=close_fds, env=env, start_new_session=True,
    )
    captures = {"stdout": bytearray(), "stderr": bytearray()}
    overflow = threading.Event()
    limit = 65_536

    def drain(pipe, key):
        while True:
            chunk = pipe.read(8192)
            if not chunk:
                return
            remaining = limit - len(captures[key])
            if remaining > 0:
                captures[key].extend(chunk[:remaining])
            if len(chunk) > remaining:
                overflow.set()

    threads = [
        threading.Thread(target=drain, args=(process.stdout, "stdout"), daemon=True),
        threading.Thread(target=drain, args=(process.stderr, "stderr"), daemon=True),
    ]
    for thread in threads:
        thread.start()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=1.0)
        raise
    finally:
        for thread in threads:
            thread.join(timeout=1.0)
    if any(thread.is_alive() for thread in threads):
        raise ExecutionGateDockerLifecycleError("Docker CLI output streams did not close")
    if overflow.is_set():
        raise ExecutionGateDockerLifecycleError("Docker CLI output exceeded its bounded capture ceiling")
    result = subprocess.CompletedProcess(
        argv, int(process.returncode), bytes(captures["stdout"]),
        bytes(captures["stderr"]),
    )
    if check and result.returncode:
        raise subprocess.CalledProcessError(
            result.returncode, argv, output=result.stdout, stderr=result.stderr,
        )
    return result
