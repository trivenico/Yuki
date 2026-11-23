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
        except ExecutionGateDockerLifecycleError:
            # The daemon may have accepted create before the client timed out.
            # Reconcile by the unique Yuki name before propagating failure.
            self.remove(container_name=container_name)
            raise
        if getattr(result, "returncode", None) != 0:
            self.remove(container_name=container_name)
            raise ExecutionGateDockerLifecycleError(
                "Docker refused to create the gate container: " + self._diagnostic(result)
            )
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
            self.remove(container_name=container_name)
            raise ExecutionGateDockerLifecycleError(
                "Docker create did not return one full daemon-assigned container ID"
            )
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
        except ExecutionGateDockerLifecycleError:
            # Start may have succeeded at the daemon before the client timed
            # out or its output exceeded the bound. Remove by unique name.
            self.remove(container_name=container.name)
            raise
        if getattr(result, "returncode", None) != 0:
            self.remove(container_name=container.name)
            raise ExecutionGateDockerLifecycleError(
                "Docker failed to start the waiting gate: " + self._diagnostic(result)
            )

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
