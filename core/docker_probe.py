"""Prepare a fixed, read-only Docker CLI diagnostics command; never execute it."""
from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from core.sandbox_runtime import SandboxRuntimeError


@dataclass(frozen=True)
class DockerProbePlan:
    """Validated command metadata, not a runtime probe or security attestation."""

    executable: str
    argv: tuple[str, ...]
    executed: bool = False
    attested: bool = False


def build_docker_info_probe(executable: str) -> DockerProbePlan:
    """Validate an explicitly supplied Docker CLI path and build fixed argv.

    This function does not search PATH, inherit Docker contexts, read daemon
    configuration, launch a process, or claim that the daemon enforces controls.
    A future caller must add bounded process supervision and independent
    host-side verification before this can contribute to runtime trust.
    """
    if os.name != "posix":
        raise SandboxRuntimeError("Docker CLI preflight currently supports POSIX hosts only")
    if type(executable) is not str or not executable or not os.path.isabs(executable):
        raise SandboxRuntimeError("Docker CLI executable must be an absolute path")
    if "\x00" in executable:
        raise SandboxRuntimeError("Docker CLI executable path contains NUL")
    path = Path(executable)
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise SandboxRuntimeError("Docker CLI executable is unavailable") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise SandboxRuntimeError("Docker CLI path must be a regular non-symlink file")
    if not os.access(str(path), os.X_OK):
        raise SandboxRuntimeError("Docker CLI file is not executable")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SandboxRuntimeError("Docker CLI path cannot be resolved safely") from exc
    if str(resolved) != executable:
        raise SandboxRuntimeError("Docker CLI path must be canonical")
    argv = (str(resolved), "info", "--format", "{{json .}}")
    return DockerProbePlan(executable=str(resolved), argv=argv)
