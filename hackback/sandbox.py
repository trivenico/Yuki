"""Guarded Docker sandbox for approved local workspace jobs.

The runner is disabled unless explicitly enabled and an exact image reference is
allow-listed. It never invokes a shell. Docker availability does not itself make
untrusted code safe; use a disposable host/workspace.
"""
from __future__ import annotations

import re
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Callable, Sequence


class SandboxError(RuntimeError):
    pass


class DockerSandbox:
    def __init__(self, workspace: str | Path, *, allowed_images: Sequence[str] = (),
                 enabled: bool = False, approver: Callable[[str], bool] | None = None,
                 docker_binary: str = "docker"):
        self.workspace = Path(workspace).expanduser().resolve()
        if "," in str(self.workspace):
            raise SandboxError("Workspace path contains unsupported Docker mount syntax.")
        if not self.workspace.is_absolute() or self.workspace == Path(self.workspace.anchor):
            raise SandboxError("A dedicated non-root workspace directory is required.")
        self.allowed_images = frozenset(allowed_images)
        self.enabled = enabled
        self.approver = approver
        self.docker_binary = docker_binary

    def run(self, image: str, argv: Sequence[str], *, timeout: int = 30, write_workspace: bool = False,
            max_output_bytes: int = 24_000) -> dict:
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
        if isinstance(max_output_bytes, bool) or not isinstance(max_output_bytes, int) or not 1 <= max_output_bytes <= 1_048_576:
            raise SandboxError("max_output_bytes must be between 1 and 1048576.")
        self.workspace.mkdir(parents=True, exist_ok=True)
        if self.approver is None or self.approver(f"Run approved image {image} with argv {list(argv)!r}") is not True:
            raise SandboxError("Explicit approval was not granted.")
        if type(write_workspace) is not bool:
            raise SandboxError("write_workspace must be a boolean.")
        mount_mode = "rw" if write_workspace else "ro"
        cid_dir = tempfile.TemporaryDirectory(prefix="yuki-docker-cid-")
        cid_file = str(Path(cid_dir.name) / "container.id")
        command = [
            self.docker_binary, "run", "--rm", "--pull=never", "--cidfile", cid_file,
            "--network=none", "--read-only", "--cap-drop=ALL",
            "--security-opt=no-new-privileges", "--pids-limit=64",
            "--memory=512m", "--cpus=1", "--user=65534:65534",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=32m",
            "--mount", f"type=bind,src={self.workspace},dst=/workspace,{mount_mode}",
            "--workdir", "/workspace", image, *argv,
        ]
        captured = {"stdout": bytearray(), "stderr": bytearray()}
        output_lock = threading.Lock()
        output_exceeded = threading.Event()
        total_captured = 0
        try:
            process = subprocess.Popen(
                command, shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
        except OSError as exc:
            cid_dir.cleanup()
            raise SandboxError("Docker could not be started.") from exc

        def cleanup_container() -> None:
            try:
                container_id = Path(cid_file).read_text(encoding="ascii").strip()
            except (OSError, UnicodeError):
                container_id = ""
            if re.fullmatch(r"[0-9a-f]{64}", container_id):
                try:
                    subprocess.run(
                        [self.docker_binary, "rm", "--force", container_id],
                        shell=False, stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        timeout=5, check=False,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    pass
            cid_dir.cleanup()

        def drain(stream, key: str) -> None:
            nonlocal total_captured
            try:
                while True:
                    chunk = stream.read(8192)
                    if not chunk:
                        return
                    with output_lock:
                        remaining = max_output_bytes - total_captured
                        if remaining > 0:
                            captured[key].extend(chunk[:remaining])
                            total_captured += min(len(chunk), remaining)
                        if len(chunk) > remaining and not output_exceeded.is_set():
                            output_exceeded.set()
                            try:
                                process.terminate()
                            except OSError:
                                pass
            finally:
                stream.close()

        readers = [
            threading.Thread(target=drain, args=(process.stdout, "stdout"), daemon=True),
            threading.Thread(target=drain, args=(process.stderr, "stderr"), daemon=True),
        ]
        for reader in readers:
            reader.start()
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.wait()
            for reader in readers:
                reader.join()
            cleanup_container()
            raise SandboxError("Sandbox process timed out.") from exc
        for reader in readers:
            reader.join()
        cleanup_container()
        if output_exceeded.is_set():
            raise SandboxError("Sandbox output exceeded the configured byte limit.")
        return {
            "returncode": returncode,
            "stdout": captured["stdout"].decode("utf-8", errors="replace"),
            "stderr": captured["stderr"].decode("utf-8", errors="replace"),
        }
