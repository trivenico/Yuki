"""Guarded Docker sandbox for approved local workspace jobs.

The runner is disabled unless explicitly enabled and an exact image reference is
allow-listed. It never invokes a shell. Docker availability does not itself make
untrusted code safe; use a disposable host/workspace.
"""
from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from typing import Callable, Sequence


class SandboxError(RuntimeError):
    pass


_OUTPUT_TAIL_BYTES = 12_000
_READ_CHUNK_BYTES = 4_096


def _drain_bounded_tail(stream, destination: bytearray, limit: int) -> None:
    """Drain a child pipe completely while retaining only its final limit bytes."""
    try:
        while True:
            chunk = stream.read(_READ_CHUNK_BYTES)
            if not chunk:
                break
            destination.extend(chunk)
            excess = len(destination) - limit
            if excess > 0:
                del destination[:excess]
    finally:
        stream.close()


class DockerSandbox:
    def __init__(self, workspace: str | Path, *, allowed_images: Sequence[str] = (),
                 enabled: bool = False, approver: Callable[[str], bool] | None = None,
                 docker_binary: str = "docker"):
        self.workspace = Path(workspace).expanduser().resolve()
        self.allowed_images = frozenset(allowed_images)
        self.enabled = enabled
        self.approver = approver
        self.docker_binary = docker_binary

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
        if self.approver is None or self.approver(f"Run approved image {image} with argv {list(argv)!r}") is not True:
            raise SandboxError("Explicit approval was not granted.")
        command = [
            self.docker_binary, "run", "--rm", "--pull=never",
            "--network=none", "--read-only", "--cap-drop=ALL",
            "--security-opt=no-new-privileges", "--pids-limit=64",
            "--memory=512m", "--cpus=1", "--user=65534:65534",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=32m",
            "--mount", f"type=bind,src={self.workspace},dst=/workspace,ro",
            "--workdir", "/workspace", image, *argv,
        ]
        stdout_tail, stderr_tail = bytearray(), bytearray()
        try:
            process = subprocess.Popen(
                command, shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
        except OSError as exc:
            raise SandboxError("Docker could not be started.") from exc

        readers = [
            threading.Thread(target=_drain_bounded_tail,
                             args=(process.stdout, stdout_tail, _OUTPUT_TAIL_BYTES),
                             daemon=True),
            threading.Thread(target=_drain_bounded_tail,
                             args=(process.stderr, stderr_tail, _OUTPUT_TAIL_BYTES),
                             daemon=True),
        ]
        for reader in readers:
            reader.start()
        timed_out = False
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            process.kill()
            process.wait()
            for reader in readers:
                reader.join()
            raise SandboxError("Sandbox process timed out.") from exc
        finally:
            if not timed_out:
                for reader in readers:
                    reader.join()

        return {
            "returncode": returncode,
            "stdout": stdout_tail.decode("utf-8", errors="replace"),
            "stderr": stderr_tail.decode("utf-8", errors="replace"),
        }
