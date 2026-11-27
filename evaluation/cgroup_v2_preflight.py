"""Read-only preflight for a delegated Linux cgroup v2 parent hierarchy.

This module never creates cgroups, enables controllers, changes permissions,
or moves processes. A successful result is only a prerequisite check; a
supervisor must still apply and independently verify limits and membership.
"""
from __future__ import annotations

from dataclasses import dataclass
import errno
import os
from pathlib import Path
import re
import stat
from typing import Callable, Mapping

_REQUIRED = frozenset({"cpu", "memory", "pids"})
_TOKEN = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class CgroupV2Preflight:
    available: bool
    root: str
    available_controllers: tuple[str, ...]
    enabled_controllers: tuple[str, ...]
    missing_controllers: tuple[str, ...]
    disabled_controllers: tuple[str, ...]
    writable: bool
    reason: str

    @property
    def ready(self) -> bool:
        return (
            self.available
            and not self.missing_controllers
            and not self.disabled_controllers
            and self.writable
            and self.reason == "ready"
        )


def _control_open_flags() -> int:
    """Require descriptor, symlink, and nonblocking protections."""
    required = ("O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK")
    missing = [name for name in required if not getattr(os, name, 0)]
    if missing:
        raise OSError(
            errno.ENOTSUP,
            "required Linux control-file flags are unavailable: "
            + ", ".join(missing),
        )
    return os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK


def _read_control_file(path: Path, *, limit: int = 4096) -> str:
    descriptor = os.open(path, _control_open_flags())
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("cgroup control path is not a regular file")
        chunks = bytearray()
        while len(chunks) <= limit:
            part = os.read(descriptor, min(1024, limit + 1 - len(chunks)))
            if not part:
                break
            chunks.extend(part)
        if len(chunks) > limit:
            raise ValueError("control file exceeds size limit")
        return chunks.decode("ascii", errors="strict").strip()
    finally:
        os.close(descriptor)


def _tokens(value: str) -> tuple[str, ...]:
    tokens = value.split()
    if any(not _TOKEN.fullmatch(token) for token in tokens):
        raise ValueError("malformed controller token")
    if len(tokens) != len(set(tokens)):
        raise ValueError("duplicate controller token")
    return tuple(sorted(tokens))


def inspect_cgroup_v2_parent(
    root: str | os.PathLike[str] = "/sys/fs/cgroup",
    *,
    required_controllers: frozenset[str] = _REQUIRED,
    access_check: Callable[[str, int], bool] = os.access,
) -> CgroupV2Preflight:
    """Inspect one already-mounted cgroup v2 parent without modifying it.

    required_controllers is intentionally limited to known safe tokens.
    access_check is injectable for deterministic permission tests; production
    callers should use the default OS permission check.
    """
    try:
        if not isinstance(required_controllers, frozenset) or not required_controllers:
            raise ValueError("required controllers must be a non-empty frozenset")
        if any(type(item) is not str or not _TOKEN.fullmatch(item)
               for item in required_controllers):
            raise ValueError("invalid required controller")
        parent = Path(root)
        resolved = parent.resolve(strict=True)
        if not resolved.is_dir():
            raise ValueError("cgroup root is not a directory")
        controllers = _tokens(_read_control_file(resolved / "cgroup.controllers"))
        enabled = _tokens(_read_control_file(resolved / "cgroup.subtree_control"))
        _read_control_file(resolved / "cgroup.procs")
        missing = tuple(sorted(required_controllers - set(controllers)))
        disabled = tuple(sorted(required_controllers - set(enabled)))
        writable = bool(access_check(str(resolved), os.W_OK | os.X_OK))
        reason = (
            "missing_controllers" if missing else
            "controllers_not_delegated" if disabled else
            "parent_not_writable" if not writable else
            "ready"
        )
        return CgroupV2Preflight(
            available=True,
            root=str(resolved),
            available_controllers=controllers,
            enabled_controllers=enabled,
            missing_controllers=missing,
            disabled_controllers=disabled,
            writable=writable,
            reason=reason,
        )
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError):
        return CgroupV2Preflight(
            available=False,
            root=str(root)[:512],
            available_controllers=(),
            enabled_controllers=(),
            missing_controllers=tuple(sorted(_REQUIRED)),
            disabled_controllers=(),
            writable=False,
            reason="unavailable_or_malformed",
        )


def _bounded_limit(value: object) -> int | None:
    if type(value) is not str:
        raise ValueError("resource limit must be text")
    if value == "max":
        return None
    if not value or not value.isascii() or not value.isdecimal():
        raise ValueError("malformed resource limit")
    number = int(value)
    if number < 0 or str(number) != value:
        raise ValueError("non-canonical resource limit")
    return number


def _cpu_limit(value: object) -> tuple[int, int] | None:
    if type(value) is not str:
        raise ValueError("cpu.max must be text")
    fields = value.split()
    if len(fields) != 2:
        raise ValueError("malformed cpu.max")
    quota = _bounded_limit(fields[0])
    period = _bounded_limit(fields[1])
    if period is None or period <= 0 or (quota is not None and quota <= 0):
        raise ValueError("invalid cpu.max")
    return None if quota is None else (quota, period)


def verify_parent_resource_ceiling(policy: object, observed: Mapping[str, str]) -> bool:
    """Compare configured parent ceilings; does not reserve runtime capacity."""
    try:
        from evaluation.runner_isolation_policy import RunnerIsolationPolicy

        if not isinstance(policy, RunnerIsolationPolicy) or not policy.verify():
            return False
        if not isinstance(observed, Mapping):
            return False
        required = {"cpu.max", "memory.max", "memory.swap.max", "pids.max"}
        if any(type(observed.get(key)) is not str for key in required):
            return False

        parent_cpu = _cpu_limit(observed["cpu.max"])
        child_cpu_quota = policy.cpu_quota_millis * 1_000
        child_cpu_period = 100_000
        if parent_cpu is not None:
            parent_quota, parent_period = parent_cpu
            if child_cpu_quota * parent_period > parent_quota * child_cpu_period:
                return False

        for key, child_value in (
            ("memory.max", policy.memory_limit_bytes),
            ("memory.swap.max", 0),
            ("pids.max", policy.pids_limit),
        ):
            parent_value = _bounded_limit(observed[key])
            if parent_value is not None and child_value > parent_value:
                return False
        return True
    except (TypeError, ValueError, AttributeError, OverflowError, RecursionError):
        return False
