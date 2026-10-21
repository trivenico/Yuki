"""Fail-closed policy model for isolated task execution.

This module validates requested sandbox capabilities. It does not itself create an OS
sandbox; callers must enforce the returned policy in a supported container/VM runtime.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet


_ALLOWED_NETWORK_MODES = frozenset({"none", "allowlist"})
_ALLOWED_CAPABILITIES = frozenset({
    "read_workspace",
    "write_workspace",
    "network_allowlist",
})


class SandboxPolicyError(ValueError):
    """Raised when a requested sandbox policy is unsafe or malformed."""


@dataclass(frozen=True)
class SandboxPolicy:
    network_mode: str = "none"
    capabilities: FrozenSet[str] = frozenset()
    timeout_seconds: int = 30
    memory_mb: int = 512
    cpu_millis: int = 1000
    max_output_bytes: int = 1_048_576
    max_processes: int = 32
    read_only_root: bool = True
    run_as_non_root: bool = True

    def validate(self) -> "SandboxPolicy":
        if self.network_mode not in _ALLOWED_NETWORK_MODES:
            raise SandboxPolicyError("unsupported network mode")
        if not isinstance(self.capabilities, (set, frozenset)):
            raise SandboxPolicyError("capabilities must be a set of strings")
        if any(not isinstance(item, str) for item in self.capabilities):
            raise SandboxPolicyError("capabilities must be strings")
        unknown = set(self.capabilities) - _ALLOWED_CAPABILITIES
        if unknown:
            raise SandboxPolicyError("unknown sandbox capability")
        if self.network_mode == "allowlist" and "network_allowlist" not in self.capabilities:
            raise SandboxPolicyError("allowlisted network requires explicit capability")
        if self.network_mode == "none" and "network_allowlist" in self.capabilities:
            raise SandboxPolicyError("network capability conflicts with network_mode=none")
        limits = {
            "timeout_seconds": (self.timeout_seconds, 1, 3600),
            "memory_mb": (self.memory_mb, 64, 65_536),
            "cpu_millis": (self.cpu_millis, 100, 64_000),
            "max_output_bytes": (self.max_output_bytes, 1, 16_777_216),
            "max_processes": (self.max_processes, 1, 1024),
        }
        for name, (value, minimum, maximum) in limits.items():
            # bool is an int subclass; reject it explicitly.
            if isinstance(value, bool) or not isinstance(value, int):
                raise SandboxPolicyError(f"{name} must be an integer")
            if not minimum <= value <= maximum:
                raise SandboxPolicyError(f"{name} is outside the permitted range")
        if self.read_only_root is not True:
            raise SandboxPolicyError("root filesystem must be read-only")
        if self.run_as_non_root is not True:
            raise SandboxPolicyError("sandbox must run as non-root")
        return self


def default_sandbox_policy() -> SandboxPolicy:
    """Return a conservative default: no network, bounded resources, non-root."""
    return SandboxPolicy().validate()
