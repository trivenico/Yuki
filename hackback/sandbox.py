"""Guarded Docker sandbox for approved local workspace jobs.

This is defense in depth, not a guarantee against container escapes. Keep Docker
patched and do not expose the Docker socket to skill code.
"""
from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Sequence


class SandboxError(RuntimeError):
    pass


class _BoundedReader(threading.Thread):
    """Drain subprocess output while retaining at most limit + 1 bytes."""

    def __init__(self, pipe, limit: int, overflow: threading.Event) -> None:
        super().__init__(daemon=True)
        self.pipe = pipe
        self.limit = limit
        self.overflow = overflow
        self.data = bytearray()

    def run(self) -> None:
        try:
            while True:
                chunk = self.pipe.read(8192)
                if not chunk:
                    break
                remaining = self.limit + 1 - len(self.data)
                if remaining > 0:
                    self.data.extend(chunk[:remaining])
                if len(chunk) > remaining or len(self.data) > self.limit:
                    self.overflow.set()
        except OSError:
            self.overflow.set()
        finally:
            self.pipe.close()


class DockerSandbox:
    """Run an explicitly approved, digest-pinned image with restrictive defaults."""

    OUTPUT_LIMIT_BYTES = 65536

    def __init__(
        self,
        workspace: str | Path,
        *,
        allowed_images: Sequence[str] = (),
        enabled: bool = False,
        approver: Callable[[str], bool] | None = None,
        docker_binary: str = "docker",
        writable_workspace: bool = False,
    ):
        self.workspace = Path(workspace).expanduser().resolve()
        if self.workspace == Path(self.workspace.anchor):
            raise ValueError("filesystem root cannot be used as a sandbox workspace")
        self.allowed_images = frozenset(allowed_images)
        self.enabled = enabled
        self.approver = approver
        self.docker_binary = docker_binary
        self.writable_workspace = writable_workspace

    def run(self, image: str, argv: Sequence[str], *, timeout: int = 30) -> dict:
        if not self.enabled:
            raise SandboxError("Sandbox execution is disabled by configuration.")
        if image not in self.allowed_images or not image or "@sha256:" not in image:
            raise SandboxError("Image must be explicitly allow-listed and pinned by digest.")
        if not isinstance(argv, (list, tuple)) or not argv or any(
            not isinstance(part, str) or not part or len(part) > 500 for part in argv
        ):
            raise SandboxError("argv must be a non-empty sequence of bounded strings.")
        if any(part in {"--privileged", "--network", "--volume", "-v", "--mount"} for part in argv):
            raise SandboxError("Container security flags cannot be supplied as program arguments.")
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 120:
            raise SandboxError("timeout must be between 1 and 120 seconds.")
        self.workspace.mkdir(parents=True, exist_ok=True)
        if not self.workspace.is_dir():
            raise SandboxError("Sandbox workspace must be a directory.")
        if self.approver is None or self.approver(
            f"Run approved image {image} with argv {list(argv)!r}"
        ) is not True:
            raise SandboxError("Explicit approval was not granted.")

        mount_mode = "rw" if self.writable_workspace else "ro"
        command = [
            self.docker_binary, "run", "--rm", "--pull=never",
            "--network=none", "--read-only", "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true", "--pids-limit=64",
            "--memory=512m", "--memory-swap=512m", "--cpus=1",
            "--user=65534:65534", "--init",
            "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=32m",
            "--mount", f"type=bind,src={self.workspace},dst=/workspace,{mount_mode}",
            "--workdir", "/workspace", image, *argv,
        ]
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                close_fds=True,
                start_new_session=(os.name == "posix"),
            )
        except OSError as exc:
            raise SandboxError("Docker could not be started.") from exc

        overflow = threading.Event()
        stdout_reader = _BoundedReader(process.stdout, self.OUTPUT_LIMIT_BYTES, overflow)
        stderr_reader = _BoundedReader(process.stderr, self.OUTPUT_LIMIT_BYTES, overflow)
        stdout_reader.start()
        stderr_reader.start()
        deadline = time.monotonic() + timeout
        timed_out = False
        try:
            while True:
                if overflow.is_set():
                    self._terminate(process)
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    self._terminate(process)
                    break
                if (
                    process.poll() is not None
                    and not stdout_reader.is_alive()
                    and not stderr_reader.is_alive()
                ):
                    break
                time.sleep(min(0.01, remaining))
        finally:
            if timed_out or overflow.is_set():
                self._terminate(process)
                try:
                    process.wait(timeout=0.25)
                except subprocess.TimeoutExpired:
                    pass
                self._kill(process)
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self._kill(process)
                process.wait()
            stdout_reader.join(timeout=1)
            stderr_reader.join(timeout=1)

        if timed_out:
            raise SandboxError("Sandbox process timed out.")
        if overflow.is_set() or len(stdout_reader.data) > self.OUTPUT_LIMIT_BYTES or len(
            stderr_reader.data
        ) > self.OUTPUT_LIMIT_BYTES:
            raise SandboxError("Sandbox output exceeded the configured limit.")
        if stdout_reader.is_alive() or stderr_reader.is_alive():
            raise SandboxError("Sandbox output readers did not stop.")
        return {
            "returncode": process.returncode,
            "stdout": bytes(stdout_reader.data).decode("utf-8", errors="replace"),
            "stderr": bytes(stderr_reader.data).decode("utf-8", errors="replace"),
        }

    @staticmethod
    def _terminate(process) -> None:
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
        except (ProcessLookupError, PermissionError, AttributeError):
            try:
                process.terminate()
            except (ProcessLookupError, OSError):
                pass

    @staticmethod
    def _kill(process) -> None:
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except (ProcessLookupError, PermissionError, AttributeError):
            try:
                process.kill()
            except (ProcessLookupError, OSError):
                pass
