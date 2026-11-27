"""Read-only preflight for a delegated Linux cgroup v2 parent hierarchy.

This module never creates cgroups, enables controllers, changes permissions,
or moves processes. A successful result is only a prerequisite check; a
supervisor must still apply and independently verify limits and membership.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
from typing import Callable

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


def _read_control_file(path: Path, *, limit: int = 4096) -> str:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
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
        # Reading cgroup.procs confirms the expected cgroup v2 control interface
        # exists; its contents are deliberately not interpreted or modified.
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
