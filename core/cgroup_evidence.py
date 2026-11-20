"""Read-only cgroup v2 limit observations; never an execution attestation."""
from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from core.sandbox_runtime import SandboxRuntimeError

_MAX_CONTROL_FILE = 4096
_CONTROLLER_RE = re.compile(r"^[a-z0-9_]+$")
_LIMIT_RE = re.compile(r"^(max|[0-9]+)$")


@dataclass(frozen=True)
class CgroupV2Limits:
    cgroup_path: str
    available_controllers: tuple[str, ...]
    cpu_quota: int | None
    cpu_period: int
    memory_max_bytes: int | None
    memory_swap_max_bytes: int | None
    pids_max: int | None
    unknown_controls: tuple[str, ...]
    observed_sources: tuple[str, ...]
    attested: bool = False


def _read_control(path: Path) -> str:
    descriptor = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(str(path), flags)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise SandboxRuntimeError("cgroup control is not a regular file")
        raw = os.read(descriptor, _MAX_CONTROL_FILE + 1)
    except SandboxRuntimeError:
        raise
    except OSError as exc:
        raise SandboxRuntimeError("required cgroup control is unavailable") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if len(raw) > _MAX_CONTROL_FILE:
        raise SandboxRuntimeError("cgroup control exceeds its size limit")
    try:
        return raw.decode("ascii", errors="strict").strip()
    except UnicodeError as exc:
        raise SandboxRuntimeError("cgroup control is not valid ASCII") from exc


def _safe_cgroup_directory(root: Path, membership: str) -> Path:
    if type(membership) is not str or not membership.startswith("/"):
        raise SandboxRuntimeError("cgroup membership must be an absolute v2 path")
    parts = membership.split("/")[1:]
    if any(part in ("", ".", "..") for part in parts):
        raise SandboxRuntimeError("cgroup membership path is not canonical")
    current = root
    for part in parts:
        current = current / part
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise SandboxRuntimeError("cgroup membership directory is unavailable") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise SandboxRuntimeError("cgroup membership contains a symlink or non-directory")
    return current


def _parse_limit(value: str, label: str) -> int | None:
    if not _LIMIT_RE.fullmatch(value):
        raise SandboxRuntimeError("cgroup " + label + " limit is malformed")
    if value == "max":
        return None
    if len(value) > 20:
        raise SandboxRuntimeError("cgroup " + label + " limit is oversized")
    return int(value)


def _parse_cpu_max(value: str) -> tuple[int | None, int]:
    fields = value.split()
    if len(fields) != 2 or not _LIMIT_RE.fullmatch(fields[0]) or not re.fullmatch(r"[0-9]{1,20}", fields[1]):
        raise SandboxRuntimeError("cgroup cpu.max is malformed")
    period = int(fields[1])
    if period <= 0:
        raise SandboxRuntimeError("cgroup cpu.max period must be positive")
    quota = None if fields[0] == "max" else _parse_limit(fields[0], "CPU")
    return quota, period


def inspect_cgroup_v2_limits(
    membership: str,
    *,
    cgroup_root: str = "/sys/fs/cgroup",
) -> CgroupV2Limits:
    """Inspect selected leaf controls without asserting that the kernel enforces them."""
    if os.name != "posix":
        raise SandboxRuntimeError("cgroup v2 inspection requires a POSIX host")
    if type(cgroup_root) is not str or not cgroup_root or not os.path.isabs(cgroup_root):
        raise SandboxRuntimeError("cgroup root must be an absolute path")
    root = Path(cgroup_root)
    try:
        root_info = root.lstat()
    except OSError as exc:
        raise SandboxRuntimeError("cgroup v2 root is unavailable") from exc
    if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
        raise SandboxRuntimeError("cgroup v2 root must be a non-symlink directory")
    leaf = _safe_cgroup_directory(root, membership)

    controllers = tuple(sorted(_read_control(root / "cgroup.controllers").split()))
    if any(not _CONTROLLER_RE.fullmatch(item) for item in controllers) or len(set(controllers)) != len(controllers):
        raise SandboxRuntimeError("cgroup controller list is malformed")
    cpu_quota, cpu_period = _parse_cpu_max(_read_control(leaf / "cpu.max"))
    memory_max = _parse_limit(_read_control(leaf / "memory.max"), "memory")
    swap_max = _parse_limit(_read_control(leaf / "memory.swap.max"), "memory swap")
    pids_max = _parse_limit(_read_control(leaf / "pids.max"), "pids")

    unknown = (
        "whether controllers are enabled for this cgroup subtree",
        "whether limits are enforced against the intended workload process",
        "CPU weight and burst policy",
        "memory pressure, OOM behavior, and descendant aggregation",
        "I/O and disk quotas",
        "cgroup delegation and writable control-file exposure",
        "host-side process identity binding",
    )
    return CgroupV2Limits(
        cgroup_path=membership,
        available_controllers=controllers,
        cpu_quota=cpu_quota,
        cpu_period=cpu_period,
        memory_max_bytes=memory_max,
        memory_swap_max_bytes=swap_max,
        pids_max=pids_max,
        unknown_controls=unknown,
        observed_sources=("cgroup2:cgroup.controllers", "cgroup2:cpu.max", "cgroup2:memory.max", "cgroup2:memory.swap.max", "cgroup2:pids.max"),
        attested=False,
    )
