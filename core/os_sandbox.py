"""Linux Bubblewrap execution backend with explicit minimal-rootfs requirements.

This backend is a prototype and is not enabled for production dispatch yet.
A rootfs must be deliberately constructed for skills and contain a
.yuki-sandbox-rootfs marker; never point it at the host root filesystem.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Sequence

from core.subprocess_executor import SubprocessExecutor


class SandboxUnavailableError(RuntimeError):
    """Raised when a platform cannot provide the requested isolation."""


class LinuxBubblewrapExecutor(SubprocessExecutor):
    """Run a skill in a minimal, read-only Linux rootfs with no network namespace.

    The only writable mount exposed to the child is a fresh per-invocation
    scratch directory at /work. The rootfs is mounted read-only. This is a
    defense-in-depth boundary, not a claim of protection against kernel or
    Bubblewrap vulnerabilities; production enablement requires independent
    review and host hardening.
    """

    def __init__(
        self,
        *,
        rootfs: str | os.PathLike[str],
        bubblewrap_path: str | None = None,
        timeout_seconds: int = 10,
        max_output_bytes: int = 65536,
    ):
        super().__init__(
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
        )
        if os.name != "posix" or not __import__("sys").platform.startswith("linux"):
            raise SandboxUnavailableError(
                "Linux Bubblewrap isolation is only available on Linux; "
                "this platform has no verified Yuki sandbox backend."
            )
        resolved = Path(rootfs).expanduser().resolve(strict=True)
        if not resolved.is_dir() or resolved == Path("/"):
            raise ValueError("rootfs must be an explicit minimal directory, not the host root.")
        marker = resolved / ".yuki-sandbox-rootfs"
        if not marker.is_file() or marker.is_symlink():
            raise ValueError("rootfs must contain a regular .yuki-sandbox-rootfs marker.")
        if resolved.stat().st_mode & 0o022:
            raise ValueError("rootfs must not be group- or world-writable.")
        binary = bubblewrap_path or shutil.which("bwrap")
        if not binary:
            raise SandboxUnavailableError("Bubblewrap (bwrap) is not installed.")
        binary_path = Path(binary).expanduser().resolve(strict=True)
        if not binary_path.is_file() or not os.access(binary_path, os.X_OK):
            raise SandboxUnavailableError("Configured Bubblewrap binary is not executable.")
        self.rootfs = str(resolved)
        self.bubblewrap_path = str(binary_path)

    def run(
        self,
        command: Sequence[str],
        payload: dict[str, Any],
        *,
        timeout_seconds: int | None = None,
        max_output_bytes: int | None = None,
    ) -> dict[str, Any]:
        if not isinstance(command, (list, tuple)) or not command or any(
            not isinstance(part, str) or not part or "\x00" in part for part in command
        ):
            raise ValueError("Command must be a non-empty list of NUL-free strings.")
        with tempfile.TemporaryDirectory(prefix="yuki-skill-") as scratch:
            sandbox_command = [
                self.bubblewrap_path,
                "--die-with-parent",
                "--new-session",
                "--unshare-all",
                "--ro-bind", self.rootfs, "/",
                "--proc", "/proc",
                "--dev", "/dev",
                "--tmpfs", "/tmp",
                "--bind", scratch, "/work",
                "--chdir", "/work",
                "--",
                *command,
            ]
            return super().run(
                sandbox_command,
                payload,
                timeout_seconds=timeout_seconds,
                max_output_bytes=max_output_bytes,
            )
