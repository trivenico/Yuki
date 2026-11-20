"""Build restrictive Docker argv from a validated sandbox policy; does not launch it."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from core.sandbox_policy import SandboxPolicy, SandboxPolicyError

_IMAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:+-]{0,200}@sha256:[0-9a-f]{64}$")


class SandboxRuntimeError(RuntimeError):
    """The requested policy cannot safely be enforced by this backend."""


class SandboxUnavailable(SandboxRuntimeError):
    """No trusted runtime satisfies the requested isolation contract."""


_REQUIRED_NAMESPACES = frozenset({"user", "pid", "mount", "ipc", "network"})
_ALLOWED_MAC = frozenset({"apparmor", "selinux"})


@dataclass(frozen=True)
class SandboxRuntimeCapabilities:
    """Runtime claims supplied by a trusted, reviewed backend adapter.

    This record is not a host probe and cannot independently prove enforcement.
    """
    runtime_name: str
    runtime_version: str
    rootless: bool
    namespaces: frozenset[str]
    seccomp: bool
    mandatory_access_control: str
    cgroups_v2: bool
    network_isolation: bool
    read_only_mounts: bool
    host_secret_exclusion: bool
    resource_limits: bool
    ephemeral_cleanup: bool
    verified_backend: bool = False

    def __post_init__(self) -> None:
        if type(self.runtime_name) is not str or not self.runtime_name.strip() or len(self.runtime_name) > 80:
            raise SandboxRuntimeError("runtime_name must be bounded non-empty text")
        if type(self.runtime_version) is not str or not self.runtime_version.strip() or len(self.runtime_version) > 80:
            raise SandboxRuntimeError("runtime_version must be bounded non-empty text")
        if type(self.namespaces) is not frozenset or not self.namespaces <= _REQUIRED_NAMESPACES:
            raise SandboxRuntimeError("namespaces must be a frozenset of recognized namespace names")
        if self.mandatory_access_control not in _ALLOWED_MAC:
            raise SandboxRuntimeError("mandatory access control must be apparmor or selinux")
        for field in (
            "rootless", "seccomp", "cgroups_v2", "network_isolation",
            "read_only_mounts", "host_secret_exclusion", "resource_limits",
            "ephemeral_cleanup", "verified_backend",
        ):
            if type(getattr(self, field)) is not bool:
                raise SandboxRuntimeError(f"{field} must be a boolean")


def validate_runtime_capabilities(
    policy: SandboxPolicy,
    capabilities: SandboxRuntimeCapabilities,
) -> None:
    """Fail closed unless a reviewed backend reports all required controls."""
    if not isinstance(policy, SandboxPolicy):
        raise TypeError("policy must be a SandboxPolicy")
    policy.validate()
    if not isinstance(capabilities, SandboxRuntimeCapabilities):
        raise TypeError("capabilities must be SandboxRuntimeCapabilities")
    missing = []
    if not capabilities.verified_backend:
        missing.append("trusted reviewed backend adapter")
    if not capabilities.rootless:
        missing.append("rootless runtime")
    if not _REQUIRED_NAMESPACES <= capabilities.namespaces:
        missing.append("user/pid/mount/ipc/network namespaces")
    for field, label in (
        ("seccomp", "seccomp"),
        ("cgroups_v2", "cgroups v2"),
        ("network_isolation", "enforced network isolation"),
        ("read_only_mounts", "read-only mounts"),
        ("host_secret_exclusion", "host secret exclusion"),
        ("resource_limits", "hard resource limits"),
        ("ephemeral_cleanup", "ephemeral cleanup"),
    ):
        if not getattr(capabilities, field):
            missing.append(label)
    if missing:
        raise SandboxUnavailable("runtime missing required controls: " + ", ".join(missing))


def build_docker_argv(
    policy: SandboxPolicy,
    *,
    image: str,
    workspace: str,
    command: Sequence[str],
    network_allowlist: Sequence[str] = (),
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
    if not isinstance(image, str) or not _IMAGE_RE.fullmatch(image):
        raise SandboxRuntimeError("sandbox image must be pinned by an immutable sha256 digest")
    if (
        not isinstance(command, (list, tuple))
        or not command
        or len(command) > 64
        or any(not isinstance(arg, str) or not arg or len(arg) > 4096 or "\x00" in arg for arg in command)
    ):
        raise SandboxRuntimeError("command must be a non-empty sequence of NUL-free strings")
    if not isinstance(workspace, str) or not os.path.isabs(workspace):
        raise SandboxRuntimeError("workspace must be an absolute path")
    path = Path(workspace).expanduser()
    if path.is_symlink() or not path.exists() or not path.is_dir():
        raise SandboxRuntimeError("workspace must be an existing non-symlink directory")
    try:
        path = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SandboxRuntimeError("workspace cannot be resolved safely") from exc
    if path == Path(path.anchor) or any(char in str(path) for char in (",", chr(10), chr(13))):
        raise SandboxRuntimeError("workspace path is unsafe for Docker mount syntax")
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

    workspace_access = "rw" if "write_workspace" in policy.capabilities else "ro"
    return [
        "docker", "run", "--rm", "--interactive", "--pull=never", "--read-only",
        "--user", f"{uid}:{gid}", "--memory", f"{policy.memory_mb}m",
        "--memory-swap", f"{policy.memory_mb}m",
        "--cpus", f"{policy.cpu_millis / 1000:.3f}",
        "--pids-limit", str(policy.max_processes), "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true",
        "--tmpfs", f"/tmp:rw,noexec,nosuid,nodev,size={min(policy.memory_mb, 256)}m",
        "--mount", f"type=bind,src={str(path.resolve())},dst=/workspace,{workspace_access}",
        "--workdir", "/workspace", *network_args, image, *command,
    ]
