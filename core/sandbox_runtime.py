"""Build restrictive Docker argv from a validated sandbox policy; does not launch it."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Sequence

from core.sandbox_policy import SandboxPolicy, SandboxPolicyError

_IMAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]{0,254}$")


class SandboxRuntimeError(RuntimeError):
    """The requested policy cannot safely be enforced by this backend."""


def build_docker_argv(
    policy: SandboxPolicy,
    *,
    image: str,
    workspace: str,
    command: Sequence[str],
    network_allowlist: Sequence[str] = (),
    docker_binary: str = "docker",
    runtime_user: tuple[int, int] | None = None,
    cidfile: str | None = None,
) -> list[str]:
    """Build a fail-closed Docker command, enforcing workspace capabilities.

    Workspace mounts are read-only unless the policy explicitly grants
    write_workspace. Network allowlisting remains unsupported without a
    separately enforced egress gateway.
    """
    try:
        policy.validate()
    except (AttributeError, SandboxPolicyError) as exc:
        raise SandboxRuntimeError("invalid sandbox policy") from exc
    if not isinstance(docker_binary, str) or not docker_binary or "\x00" in docker_binary:
        raise SandboxRuntimeError("invalid Docker executable")
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
    if not path.exists() or not path.is_dir():
        raise SandboxRuntimeError("workspace must be an existing directory")
    resolved_workspace = str(path.resolve())
    # Docker parses --mount as comma-separated key/value fields. A comma in
    # the host path could inject mount options and weaken the intended scope.
    if "," in resolved_workspace:
        raise SandboxRuntimeError("workspace path contains unsupported mount syntax")
    if policy.network_mode == "none":
        if network_allowlist:
            raise SandboxRuntimeError("allowlist conflicts with disabled networking")
        network_args = ["--network", "none"]
    else:
        raise SandboxRuntimeError("allowlist mode requires a separately enforced egress gateway")
    host_uid = os.getuid() if hasattr(os, "getuid") else None
    if host_uid == 0:
        raise SandboxRuntimeError("refusing sandbox launch from a root service")
    if runtime_user is None:
        if not hasattr(os, "getuid") or not hasattr(os, "getgid"):
            raise SandboxRuntimeError("non-root UID/GID detection unsupported on this host")
        uid, gid = host_uid, os.getgid()
    else:
        if (not isinstance(runtime_user, tuple) or len(runtime_user) != 2
                or any(isinstance(value, bool) or not isinstance(value, int) or value < 1
                       for value in runtime_user)):
            raise SandboxRuntimeError("runtime_user must be a non-root (uid, gid) pair")
        uid, gid = runtime_user
    if uid == 0 or gid == 0:
        raise SandboxRuntimeError("refusing root container identity")
    cid_args: list[str] = []
    if cidfile is not None:
        if not isinstance(cidfile, str) or not os.path.isabs(cidfile) or "," in cidfile or "\x00" in cidfile:
            raise SandboxRuntimeError("cidfile must be an absolute path without mount delimiters")
        cid_args = ["--cidfile", cidfile]

    workspace_access = "rw" if "write_workspace" in policy.capabilities else "ro"
    return [
        docker_binary, "run", "--rm", "--pull=never", *cid_args, "--interactive", "--read-only",
        "--user", f"{uid}:{gid}", "--memory", f"{policy.memory_mb}m",
        "--cpus", f"{policy.cpu_millis / 1000:.3f}",
        "--pids-limit", str(policy.max_processes), "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true",
        "--tmpfs", f"/tmp:rw,noexec,nosuid,nodev,size={min(policy.memory_mb, 256)}m",
        "--mount", f"type=bind,src={resolved_workspace},dst=/workspace,{workspace_access}",
        "--workdir", "/workspace", *network_args, image, *command,
    ]
