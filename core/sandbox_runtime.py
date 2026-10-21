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

def build_docker_argv(policy: SandboxPolicy, *, image: str, workspace: str,
                      command: Sequence[str], network_allowlist: Sequence[str] = ()) -> list[str]:
    try:
        policy.validate()
    except (AttributeError, SandboxPolicyError) as exc:
        raise SandboxRuntimeError("invalid sandbox policy") from exc
    if not isinstance(image, str) or not _IMAGE_RE.fullmatch(image):
        raise SandboxRuntimeError("invalid sandbox image reference")
    if not isinstance(command, (list, tuple)) or not command:
        raise SandboxRuntimeError("command must be a non-empty argument sequence")
    if any(not isinstance(arg, str) or "\x00" in arg for arg in command):
        raise SandboxRuntimeError("command arguments must be NUL-free strings")
    if not isinstance(workspace, str) or not os.path.isabs(workspace):
        raise SandboxRuntimeError("workspace must be an absolute path")
    path = Path(workspace)
    if not path.exists() or not path.is_dir():
        raise SandboxRuntimeError("workspace must be an existing directory")
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
    return [
        "docker", "run", "--rm", "--interactive", "--read-only",
        "--user", f"{uid}:{gid}", "--memory", f"{policy.memory_mb}m",
        "--cpus", f"{policy.cpu_millis / 1000:.3f}",
        "--pids-limit", str(policy.max_processes), "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true",
        "--tmpfs", f"/tmp:rw,noexec,nosuid,nodev,size={min(policy.memory_mb, 256)}m",
        "--mount", f"type=bind,src={str(path.resolve())},dst=/workspace,rw",
        "--workdir", "/workspace", *network_args, image, *command,
    ]
