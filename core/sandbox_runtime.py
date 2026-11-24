"""Build restrictive Docker argv from a validated sandbox policy; does not launch it."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from core.sandbox_policy import SandboxPolicy, SandboxPolicyError

_IMAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:+-]{0,200}@sha256:[0-9a-f]{64}$")
_DEFAULT_DOCKER_BINARY = "/usr/bin/docker"


class SandboxRuntimeError(RuntimeError):
    """The requested policy cannot safely be enforced by this backend."""


class SandboxUnavailable(SandboxRuntimeError):
    """No trusted runtime satisfies the requested isolation contract."""


_REQUIRED_NAMESPACES = frozenset({"user", "pid", "mount", "ipc", "network"})
_ALLOWED_MAC = frozenset({"apparmor", "selinux"})


@dataclass(frozen=True)
class SandboxRuntimeCapabilities:
    """Untrusted capability declarations, never proof of enforcement.

    These values are diagnostic metadata only. A trusted host-side verifier
    is not implemented, so callers cannot promote these claims into attestation.
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

    def __post_init__(self) -> None:
        if type(self.runtime_name) is not str or not self.runtime_name.strip() or len(self.runtime_name) > 80:
            raise SandboxRuntimeError("runtime_name must be bounded non-empty text")
        if type(self.runtime_version) is not str or not self.runtime_version.strip() or len(self.runtime_version) > 80:
            raise SandboxRuntimeError("runtime_version must be bounded non-empty text")
        if type(self.namespaces) is not frozenset or not self.namespaces <= _REQUIRED_NAMESPACES:
            raise SandboxRuntimeError("namespaces must be a frozenset of recognized namespace names")
        if type(self.mandatory_access_control) is not str or self.mandatory_access_control not in _ALLOWED_MAC:
            raise SandboxRuntimeError("mandatory access control must be apparmor or selinux")
        for field in (
            "rootless", "seccomp", "cgroups_v2", "network_isolation",
            "read_only_mounts", "host_secret_exclusion", "resource_limits",
            "ephemeral_cleanup",
        ):
            if type(getattr(self, field)) is not bool:
                raise SandboxRuntimeError(f"{field} must be a boolean")


@dataclass(frozen=True)
class DockerDaemonDiagnostics:
    """Read-only facts parsed from Docker's info JSON; never an attestation."""
    server_version: str
    rootless_reported: bool
    seccomp_reported: bool
    apparmor_reported: bool
    selinux_reported: bool
    cgroups_v2_reported: bool
    unsupported_controls: tuple[str, ...]


def parse_docker_info(payload: str) -> DockerDaemonDiagnostics:
    """Parse bounded Docker info JSON without promoting daemon claims to trust."""
    import json

    if type(payload) is not str or not payload:
        raise SandboxRuntimeError("Docker info payload must be non-empty text")
    try:
        encoded_size = len(payload.encode("utf-8", errors="strict"))
    except UnicodeError as exc:
        raise SandboxRuntimeError("Docker info payload contains invalid Unicode") from exc
    if encoded_size > 262_144:
        raise SandboxRuntimeError("Docker info payload must be at most 256 KiB")

    def reject_duplicate_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON object key")
            result[key] = value
        return result

    try:
        data = json.loads(payload, object_pairs_hook=reject_duplicate_keys)
    except (ValueError, UnicodeError) as exc:
        raise SandboxRuntimeError("Docker info payload is not valid unambiguous JSON") from exc
    if type(data) is not dict:
        raise SandboxRuntimeError("Docker info payload must be a JSON object")

    version = data.get("ServerVersion")
    security_options = data.get("SecurityOptions")
    cgroup_version = data.get("CgroupVersion")
    if type(version) is not str or not version or len(version) > 80:
        raise SandboxRuntimeError("Docker info is missing a valid ServerVersion")
    if type(security_options) is not list or len(security_options) > 64:
        raise SandboxRuntimeError("Docker info has invalid SecurityOptions")
    if any(type(item) is not str or len(item) > 256 for item in security_options):
        raise SandboxRuntimeError("Docker SecurityOptions contains invalid entries")
    if type(cgroup_version) is not str or len(cgroup_version) > 16:
        raise SandboxRuntimeError("Docker info has invalid CgroupVersion")

    options = tuple(item.lower() for item in security_options)
    rootless = any("name=rootless" in item or item.strip() == "rootless" for item in options)
    seccomp = any("name=seccomp" in item or item.strip() == "seccomp" for item in options)
    apparmor = any("name=apparmor" in item or item.strip() == "apparmor" for item in options)
    selinux = any("name=selinux" in item or item.strip() == "selinux" for item in options)
    cgroups_v2 = cgroup_version == "2"

    unsupported = (
        "user/pid/mount/ipc/network namespace enforcement",
        "per-container seccomp profile and syscall coverage",
        "mandatory access-control enforcement for this container",
        "network egress isolation",
        "read-only mount enforcement and workspace mount audit",
        "host secret exclusion",
        "hard resource-limit enforcement",
        "ephemeral container cleanup",
        "trusted runtime probe provenance",
    )
    return DockerDaemonDiagnostics(
        server_version=version, rootless_reported=rootless,
        seccomp_reported=seccomp, apparmor_reported=apparmor,
        selinux_reported=selinux, cgroups_v2_reported=cgroups_v2,
        unsupported_controls=unsupported,
    )


def validate_runtime_capabilities(policy: SandboxPolicy, capabilities: SandboxRuntimeCapabilities) -> None:
    """Reject declarations until a trusted host-side verifier exists."""
    if not isinstance(policy, SandboxPolicy):
        raise TypeError("policy must be a SandboxPolicy")
    policy.validate()
    if not isinstance(capabilities, SandboxRuntimeCapabilities):
        raise TypeError("capabilities must be SandboxRuntimeCapabilities")
    missing = [
        "trusted host-side runtime probe/attestation (not implemented)",
        "verified mandatory access-control enforcement (not established by declarations)",
    ]
    if not capabilities.rootless:
        missing.append("rootless runtime")
    if not _REQUIRED_NAMESPACES <= capabilities.namespaces:
        missing.append("user/pid/mount/ipc/network namespaces")
    for field, label in (
        ("seccomp", "seccomp"), ("cgroups_v2", "cgroups v2"),
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
    docker_binary: str = _DEFAULT_DOCKER_BINARY,
) -> list[str]:
    """Build a fail-closed Docker command using a pinned executable path."""
    try:
        policy.validate()
    except (AttributeError, SandboxPolicyError) as exc:
        raise SandboxRuntimeError("invalid sandbox policy") from exc
    if (not isinstance(docker_binary, str) or not os.path.isabs(docker_binary)
            or "\x00" in docker_binary or len(docker_binary) > 4096):
        raise SandboxRuntimeError("Docker executable must be an absolute, bounded path")
    if not isinstance(image, str) or not _IMAGE_RE.fullmatch(image):
        raise SandboxRuntimeError("sandbox image must be pinned by an immutable sha256 digest")
    if (
        not isinstance(command, (list, tuple)) or not command or len(command) > 64
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
    if not isinstance(network_allowlist, (list, tuple)) or any(not isinstance(item, str) or not item for item in network_allowlist):
        raise SandboxRuntimeError("network_allowlist must be a sequence of non-empty strings")
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
        docker_binary, "run", "--rm", "--init", "--interactive", "--pull=never", "--read-only",
        "--user", f"{uid}:{gid}", "--memory", f"{policy.memory_mb}m",
        "--memory-swap", f"{policy.memory_mb}m",
        "--cpus", f"{policy.cpu_millis / 1000:.3f}",
        "--pids-limit", str(policy.max_processes), "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true",
        "--tmpfs", f"/tmp:rw,noexec,nosuid,nodev,size={min(policy.memory_mb, 256)}m",
        "--mount", f"type=bind,src={str(path.resolve())},dst=/workspace,{workspace_access}",
        "--workdir", "/workspace", *network_args, image, *command,
    ]
