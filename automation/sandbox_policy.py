"""Fail-closed, immutable policy contract for isolated developer workloads.

This module defines policy and capability data only. It does not launch containers,
inspect the host, or prove that a runtime enforces the declared capabilities.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Iterable


class SandboxPolicyError(ValueError):
    """A sandbox policy or runtime capability declaration is invalid."""


class SandboxUnavailable(RuntimeError):
    """No verified runtime satisfies the requested isolation policy."""


_IMAGE_DIGEST = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._/:+-]{0,200}@sha256:[0-9a-f]{64}$")
_MODULE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
_NAMESPACE_SET = frozenset({"user", "pid", "mount", "ipc", "network"})
_MAC_PROFILES = frozenset({"apparmor", "selinux"})


@dataclass(frozen=True)
class SandboxResourceLimits:
    """Hard upper bounds requested from the isolation backend."""

    cpu_millis: int = 1000
    memory_mb: int = 1024
    pids: int = 64
    disk_mb: int = 1024
    timeout_seconds: int = 120
    output_bytes: int = 65536
    artifact_bytes: int = 10485760

    def __post_init__(self) -> None:
        bounds = {
            "cpu_millis": (100, 16000),
            "memory_mb": (128, 32768),
            "pids": (1, 1024),
            "disk_mb": (64, 32768),
            "timeout_seconds": (1, 3600),
            "output_bytes": (1024, 1048576),
            "artifact_bytes": (0, 104857600),
        }
        for field, (minimum, maximum) in bounds.items():
            value = getattr(self, field)
            if type(value) is not int or not minimum <= value <= maximum:
                raise SandboxPolicyError(
                    f"{field} must be an integer between {minimum} and {maximum}."
                )


@dataclass(frozen=True)
class SandboxExecutionPolicy:
    """Immutable restrictions for one narrowly scoped developer workload."""

    image: str
    test_module: str
    policy_version: str = "1"
    network_disabled: bool = True
    source_read_only: bool = True
    no_host_secrets: bool = True
    no_host_network: bool = True
    no_privileged_mode: bool = True
    drop_all_capabilities: bool = True
    no_new_privileges: bool = True
    read_only_rootfs: bool = True
    ephemeral: bool = True
    limits: SandboxResourceLimits = SandboxResourceLimits()

    def __post_init__(self) -> None:
        if not isinstance(self.image, str) or not _IMAGE_DIGEST.fullmatch(self.image):
            raise SandboxPolicyError("image must be pinned by an immutable sha256 digest.")
        if not isinstance(self.test_module, str) or not _MODULE.fullmatch(self.test_module):
            raise SandboxPolicyError("test_module must be a valid dotted Python module.")
        if not isinstance(self.policy_version, str) or not re.fullmatch(r"[0-9]{1,4}", self.policy_version):
            raise SandboxPolicyError("policy_version must be a bounded numeric version.")
        for field in (
            "network_disabled", "source_read_only", "no_host_secrets",
            "no_host_network", "no_privileged_mode", "drop_all_capabilities",
            "no_new_privileges", "read_only_rootfs", "ephemeral",
        ):
            if getattr(self, field) is not True:
                raise SandboxPolicyError(f"{field} is mandatory and cannot be disabled.")
        if not isinstance(self.limits, SandboxResourceLimits):
            raise SandboxPolicyError("limits must be SandboxResourceLimits.")

    def canonical_payload(self) -> dict:
        return {
            "policy_version": self.policy_version,
            "image": self.image,
            "test_module": self.test_module,
            "network_disabled": self.network_disabled,
            "source_read_only": self.source_read_only,
            "no_host_secrets": self.no_host_secrets,
            "no_host_network": self.no_host_network,
            "no_privileged_mode": self.no_privileged_mode,
            "drop_all_capabilities": self.drop_all_capabilities,
            "no_new_privileges": self.no_new_privileges,
            "read_only_rootfs": self.read_only_rootfs,
            "ephemeral": self.ephemeral,
            "limits": {
                "cpu_millis": self.limits.cpu_millis,
                "memory_mb": self.limits.memory_mb,
                "pids": self.limits.pids,
                "disk_mb": self.limits.disk_mb,
                "timeout_seconds": self.limits.timeout_seconds,
                "output_bytes": self.limits.output_bytes,
                "artifact_bytes": self.limits.artifact_bytes,
            },
        }

    @property
    def digest(self) -> str:
        encoded = json.dumps(
            self.canonical_payload(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class SandboxRuntimeCapabilities:
    """Backend-reported features; declarations are not proof of enforcement."""

    runtime_name: str
    runtime_version: str
    rootless: bool
    namespaces: frozenset[str]
    seccomp: bool
    mandatory_access_control: str
    cgroups_v2: bool
    network_isolation: bool
    read_only_mounts: bool
    secret_mount_exclusion: bool
    resource_limits: bool
    ephemeral_cleanup: bool
    verified_backend: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.runtime_name, str) or not self.runtime_name.strip() or len(self.runtime_name) > 80:
            raise SandboxPolicyError("runtime_name must be bounded non-empty text.")
        if not isinstance(self.runtime_version, str) or not self.runtime_version.strip() or len(self.runtime_version) > 80:
            raise SandboxPolicyError("runtime_version must be bounded non-empty text.")
        if not isinstance(self.namespaces, frozenset) or not self.namespaces <= _NAMESPACE_SET:
            raise SandboxPolicyError("namespaces must be a frozenset of recognized namespaces.")
        if self.mandatory_access_control not in _MAC_PROFILES:
            raise SandboxPolicyError("mandatory_access_control must be apparmor or selinux.")
        for field in (
            "rootless", "seccomp", "cgroups_v2", "network_isolation",
            "read_only_mounts", "secret_mount_exclusion", "resource_limits",
            "ephemeral_cleanup", "verified_backend",
        ):
            if type(getattr(self, field)) is not bool:
                raise SandboxPolicyError(f"{field} must be a boolean.")


_REQUIRED_NAMESPACES = frozenset({"user", "pid", "mount", "ipc", "network"})


def validate_runtime_capabilities(
    policy: SandboxExecutionPolicy,
    capabilities: SandboxRuntimeCapabilities,
) -> None:
    """Fail closed unless a separately verified backend reports every required control."""
    if not isinstance(policy, SandboxExecutionPolicy):
        raise TypeError("policy must be a SandboxExecutionPolicy.")
    if not isinstance(capabilities, SandboxRuntimeCapabilities):
        raise TypeError("capabilities must be SandboxRuntimeCapabilities.")
    missing = []
    if not capabilities.verified_backend:
        missing.append("independently verified backend")
    if not capabilities.rootless:
        missing.append("rootless execution")
    if not _REQUIRED_NAMESPACES <= capabilities.namespaces:
        missing.append("user/pid/mount/ipc/network namespaces")
    for field, label in (
        ("seccomp", "seccomp"),
        ("cgroups_v2", "cgroups v2"),
        ("network_isolation", "enforced network isolation"),
        ("read_only_mounts", "read-only mounts"),
        ("secret_mount_exclusion", "host secret exclusion"),
        ("resource_limits", "resource limits"),
        ("ephemeral_cleanup", "ephemeral cleanup"),
    ):
        if not getattr(capabilities, field):
            missing.append(label)
    if missing:
        raise SandboxUnavailable("runtime does not satisfy required controls: " + ", ".join(missing))
