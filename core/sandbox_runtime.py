"""Build restrictive Docker argv from a validated sandbox policy; does not launch it."""
from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import Sequence

from core.sandbox_policy import SandboxPolicy, SandboxPolicyError

_IMAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]{0,254}$")


class SandboxRuntimeError(RuntimeError):
    """The requested policy cannot safely be enforced by this backend."""


def _validate_artifact_output_directory(value: str, *, workspace: Path, uid: int) -> Path:
    """Accept only a private, service-owned directory outside the trusted workspace."""
    if not isinstance(value, str) or not os.path.isabs(value):
        raise SandboxRuntimeError("artifact output directory must be an absolute path")
    candidate = Path(value)
    try:
        info = candidate.lstat()
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise SandboxRuntimeError("artifact output directory must already exist") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise SandboxRuntimeError("artifact output path must be a real directory")
    if info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o700:
        raise SandboxRuntimeError("artifact output directory must be service-owned with mode 0700")
    trusted = workspace.resolve()
    if resolved == trusted or resolved in trusted.parents or trusted in resolved.parents:
        raise SandboxRuntimeError("artifact output directory must be isolated from trusted workspace")
    return resolved


def build_docker_argv(
    policy: SandboxPolicy,
    *,
    image: str,
    workspace: str,
    command: Sequence[str],
    network_allowlist: Sequence[str] = (),
    artifact_output_directory: str | None = None,
) -> list[str]:
    """Build a fail-closed Docker command with optional isolated artifact output.

    The trusted workspace is always read-only. When supplied, artifact output is
    a separate, service-owned 0700 directory mounted writable only at /output.
    The container runs as the service UID/GID so it can write that directory;
    this is not a general workspace-write capability.
    """
    try:
        policy.validate()
    except (AttributeError, SandboxPolicyError) as exc:
        raise SandboxRuntimeError("invalid sandbox policy") from exc
    if not isinstance(image, str) or not _IMAGE_RE.fullmatch(image):
        raise SandboxRuntimeError("invalid sandbox image reference")
    if (
        not isinstance(command, (list, tuple))
        or not command
        or any(not isinstance(arg, str) or "\x00" in arg for arg in command)
    ):
        raise SandboxRuntimeError("command must be a non-empty sequence of NUL-free strings")
    if not isinstance(workspace, str) or not os.path.isabs(workspace):
        raise SandboxRuntimeError("workspace must be an absolute path")
    path = Path(workspace)
    if not path.exists() or not path.is_dir() or path.is_symlink():
        raise SandboxRuntimeError("workspace must be an existing non-symlink directory")
    if policy.network_mode == "none":
        if network_allowlist:
            raise SandboxRuntimeError("allowlist conflicts with disabled networking")
        network_args = ["--network", "none"]
    else:
        raise SandboxRuntimeError("allowlist mode requires a separately enforced egress gateway")
    if not hasattr(os, "getuid") or not hasattr(os, "getgid"):
        raise SandboxRuntimeError("non-root UID/GID detection unsupported on this host")
    uid, gid = os.getuid(), os.getgid()
    if uid == 0:
        raise SandboxRuntimeError("refusing sandbox launch from a root service")
    if "write_workspace" in policy.capabilities:
        raise SandboxRuntimeError("trusted workspace is always mounted read-only")

    mounts = ["--mount", f"type=bind,src={str(path.resolve())},dst=/workspace,ro"]
    if artifact_output_directory is not None:
        output = _validate_artifact_output_directory(
            artifact_output_directory, workspace=path, uid=uid
        )
        mounts.extend(["--mount", f"type=bind,src={str(output)},dst=/output,rw"])

    return [
        "docker", "run", "--rm", "--interactive", "--read-only",
        "--user", f"{uid}:{gid}", "--memory", f"{policy.memory_mb}m",
        "--cpus", f"{policy.cpu_millis / 1000:.3f}",
        "--pids-limit", str(policy.max_processes), "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true",
        "--tmpfs", f"/tmp:rw,noexec,nosuid,nodev,size={min(policy.memory_mb, 256)}m",
        *mounts, "--workdir", "/workspace", *network_args, image, *command,
    ]
