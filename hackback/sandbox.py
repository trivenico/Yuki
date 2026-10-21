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


class DockerSandbox:
    def __init__(self, workspace: str | Path, *, allowed_images: Sequence[str] = (),
                 enabled: bool = False, approver: Callable[[str], bool] | None = None,
                 docker_binary: str = "docker", max_output_bytes: int = 12000):
        self.workspace = Path(workspace).expanduser().resolve()
        self.allowed_images = frozenset(allowed_images)
        self.enabled = enabled
        self.approver = approver
        self.docker_binary = docker_binary
        if isinstance(max_output_bytes, bool) or not isinstance(max_output_bytes, int) or not 1 <= max_output_bytes <= 1_048_576:
            raise ValueError("max_output_bytes must be between 1 and 1048576.")
        self.max_output_bytes = max_output_bytes

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
            "--mount", f"type=bind,src={self.workspace},dst=/workspace,rw",
            "--workdir", "/workspace", image, *argv,
        ]
        captured = {
            "stdout": bytearray(),
            "stderr": bytearray(),
        }
        truncated = {"stdout": False, "stderr": False}

        def drain(name: str, stream) -> None:
            total = 0
            try:
                while True:
                    chunk = stream.read(4096)
                    if not chunk:
                        break
                    total += len(chunk)
                    captured[name].extend(chunk)
                    if len(captured[name]) > self.max_output_bytes:
                        del captured[name][:-self.max_output_bytes]
                    if total > self.max_output_bytes:
                        truncated[name] = True
            finally:
                stream.close()

        try:
            process = subprocess.Popen(
                command,
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
            readers = [
                threading.Thread(target=drain, args=("stdout", process.stdout), daemon=True),
                threading.Thread(target=drain, args=("stderr", process.stderr), daemon=True),
            ]
            for reader in readers:
                reader.start()
            try:
                returncode = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                process.kill()
                process.wait()
                for reader in readers:
                    reader.join(timeout=2)
                raise SandboxError("Sandbox process timed out.") from exc
            for reader in readers:
                reader.join()
        except SandboxError:
            raise
        except OSError as exc:
            raise SandboxError("Docker could not be started.") from exc

        return {
            "returncode": returncode,
            "stdout": bytes(captured["stdout"]).decode("utf-8", errors="replace"),
            "stderr": bytes(captured["stderr"]).decode("utf-8", errors="replace"),
            "stdout_truncated": truncated["stdout"],
            "stderr_truncated": truncated["stderr"],
        }
