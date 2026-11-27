"""Fail-closed policy contract for a future isolated Linux evaluation runner.

This module validates and fingerprints requested isolation settings. It does NOT
launch processes or enforce namespaces, cgroups, seccomp, mounts, or credentials.
A production supervisor must independently apply and verify every control.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import re
from typing import Any, Mapping

_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_SCHEMA = "yuki.runner-isolation-policy.v1"


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


@dataclass(frozen=True, slots=True)
class RunnerIsolationPolicy:
    schema: str
    uid: int
    gid: int
    cpu_quota_millis: int
    memory_limit_bytes: int
    pids_limit: int
    wall_time_seconds: int
    output_limit_bytes: int
    tmpfs_limit_bytes: int
    seccomp_profile_digest: str
    network_mode: str
    read_only_rootfs: bool
    no_new_privileges: bool
    drop_all_capabilities: bool
    policy_digest: str

    def cgroup_v2_settings(self) -> dict[str, str]:
        """Render validated resource bounds in Linux cgroup v2 file syntax.

        This is configuration data only: callers must create a dedicated
        cgroup, write these values, verify read-back, and prove the child is
        inside that cgroup before allowing execution.
        """
        if not self.verify():
            raise ValueError("runner isolation policy is invalid")
        period_micros = 100_000
        quota_micros = self.cpu_quota_millis * 1_000
        return {
            "cpu.max": f"{quota_micros} {period_micros}",
            "memory.max": str(self.memory_limit_bytes),
            "memory.swap.max": "0",
            "pids.max": str(self.pids_limit),
        }

    def payload(self) -> dict[str, Any]:
        fields = asdict(self)
        fields.pop("policy_digest")
        return fields

    def verify(self) -> bool:
        try:
            if type(self.schema) is not str or self.schema != _SCHEMA:
                return False
            integer_bounds = (
                (self.uid, 1, 2**31 - 1),
                (self.gid, 1, 2**31 - 1),
                (self.cpu_quota_millis, 1, 60_000),
                (self.memory_limit_bytes, 64 * 1024**2, 4 * 1024**3),
                (self.pids_limit, 1, 64),
                (self.wall_time_seconds, 1, 3600),
                (self.output_limit_bytes, 1, 1024**2),
                (self.tmpfs_limit_bytes, 0, 256 * 1024**2),
            )
            for value, minimum, maximum in integer_bounds:
                if type(value) is not int or not minimum <= value <= maximum:
                    return False
            if type(self.seccomp_profile_digest) is not str or not _DIGEST.fullmatch(self.seccomp_profile_digest):
                return False
            if type(self.policy_digest) is not str or not _DIGEST.fullmatch(self.policy_digest):
                return False
            if type(self.network_mode) is not str or self.network_mode != "none":
                return False
            if any(type(value) is not bool for value in (
                self.read_only_rootfs, self.no_new_privileges, self.drop_all_capabilities
            )):
                return False
            if not (self.read_only_rootfs and self.no_new_privileges and self.drop_all_capabilities):
                return False
            return hashlib.sha256(_canonical(self.payload())).hexdigest() == self.policy_digest
        except (TypeError, ValueError, AttributeError, RecursionError):
            return False


def verify_cgroup_v2_readback(
    policy: RunnerIsolationPolicy, observed: Mapping[str, str]
) -> bool:
    """Verify kernel read-back matches every required resource limit exactly.

    The mapping should contain values read from the dedicated cgroup's
    cpu.max, memory.max, memory.swap.max, and pids.max files. This verifies
    configuration equality only; it does not prove task membership, controller
    activation, hierarchy inheritance, or that the values remained in force.
    """
    try:
        if not isinstance(policy, RunnerIsolationPolicy) or not policy.verify():
            return False
        if not isinstance(observed, Mapping):
            return False
        expected = policy.cgroup_v2_settings()
        for name, value in expected.items():
            actual = observed.get(name)
            if type(actual) is not str or actual != value:
                return False
        return True
    except (TypeError, ValueError, AttributeError, RuntimeError):
        return False


def create_runner_isolation_policy(
    *, uid: int, gid: int, seccomp_profile_digest: str,
    cpu_quota_millis: int = 1000,
    memory_limit_bytes: int = 512 * 1024**2,
    pids_limit: int = 16,
    wall_time_seconds: int = 300,
    output_limit_bytes: int = 256 * 1024,
    tmpfs_limit_bytes: int = 64 * 1024**2,
) -> RunnerIsolationPolicy:
    """Create a canonical policy; root identities and permissive defaults fail closed."""
    fields = {
        "schema": _SCHEMA,
        "uid": uid,
        "gid": gid,
        "cpu_quota_millis": cpu_quota_millis,
        "memory_limit_bytes": memory_limit_bytes,
        "pids_limit": pids_limit,
        "wall_time_seconds": wall_time_seconds,
        "output_limit_bytes": output_limit_bytes,
        "tmpfs_limit_bytes": tmpfs_limit_bytes,
        "seccomp_profile_digest": seccomp_profile_digest,
        "network_mode": "none",
        "read_only_rootfs": True,
        "no_new_privileges": True,
        "drop_all_capabilities": True,
    }
    candidate = RunnerIsolationPolicy(
        **fields, policy_digest=hashlib.sha256(_canonical(fields)).hexdigest()
    )
    if not candidate.verify():
        raise ValueError("runner isolation policy is invalid or exceeds safe bounds")
    return candidate
