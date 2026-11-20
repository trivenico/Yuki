"""Read-only Linux user-namespace ID-map observations; never attestation."""
from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from core.linux_host_evidence import _parse_start_time, _read_bounded
from core.sandbox_runtime import SandboxRuntimeError

_MAX_ID = (1 << 32) - 1
_MAX_MAP_LINES = 340


@dataclass(frozen=True)
class LinuxIdMapRange:
    inside_start: int
    outside_start: int
    length: int


@dataclass(frozen=True)
class LinuxUserNamespaceSnapshot:
    pid: int
    start_time_ticks: int
    uid_map: tuple[LinuxIdMapRange, ...]
    gid_map: tuple[LinuxIdMapRange, ...]
    setgroups: str | None
    observed_sources: tuple[str, ...]
    unknown_controls: tuple[str, ...]
    evidence_class: str = "diagnostic_only"
    attested: bool = False
    verified: bool = False


def _parse_id_map(payload: str, label: str) -> tuple[LinuxIdMapRange, ...]:
    lines = payload.splitlines()
    if not lines or len(lines) > _MAX_MAP_LINES:
        raise SandboxRuntimeError("user namespace " + label + " map has invalid line count")
    ranges: list[LinuxIdMapRange] = []
    for line in lines:
        fields = line.split()
        if len(fields) != 3 or any(not item.isascii() or not item.isdecimal() for item in fields):
            raise SandboxRuntimeError("user namespace " + label + " map contains malformed range")
        inside, outside, length = (int(item) for item in fields)
        if length <= 0 or inside > _MAX_ID or outside > _MAX_ID:
            raise SandboxRuntimeError("user namespace " + label + " map range is out of bounds")
        if inside + length - 1 > _MAX_ID or outside + length - 1 > _MAX_ID:
            raise SandboxRuntimeError("user namespace " + label + " map range overflows")
        ranges.append(LinuxIdMapRange(inside, outside, length))
    ordered_inside = sorted(ranges, key=lambda item: item.inside_start)
    ordered_outside = sorted(ranges, key=lambda item: item.outside_start)
    for ordered in (ordered_inside, ordered_outside):
        for previous, current in zip(ordered, ordered[1:]):
            if previous.inside_start + previous.length > current.inside_start if ordered is ordered_inside else previous.outside_start + previous.length > current.outside_start:
                raise SandboxRuntimeError("user namespace " + label + " map contains overlapping ranges")
    return tuple(ranges)


def collect_linux_user_namespace_snapshot(
    pid: int, *, proc_root: str = "/proc"
) -> LinuxUserNamespaceSnapshot:
    """Collect UID/GID mapping files and setgroups policy from a process proc entry."""
    if os.name != "posix":
        raise SandboxRuntimeError("Linux user namespace observations require a POSIX host")
    if type(pid) is not int or pid <= 0:
        raise SandboxRuntimeError("process ID must be a positive integer")
    if type(proc_root) is not str or not proc_root or not os.path.isabs(proc_root):
        raise SandboxRuntimeError("procfs root must be an absolute path")
    root = Path(proc_root)
    try:
        root_info = root.lstat()
        process = root / str(pid)
        process_info = process.lstat()
    except OSError as exc:
        raise SandboxRuntimeError("procfs process entry is unavailable") from exc
    if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
        raise SandboxRuntimeError("procfs root must be a non-symlink directory")
    if stat.S_ISLNK(process_info.st_mode) or not stat.S_ISDIR(process_info.st_mode):
        raise SandboxRuntimeError("procfs process entry must be a non-symlink directory")

    first_start = _parse_start_time(_read_bounded(process / "stat"))
    uid_map = _parse_id_map(_read_bounded(process / "uid_map"), "UID")
    gid_map = _parse_id_map(_read_bounded(process / "gid_map"), "GID")
    setgroups_path = process / "setgroups"
    try:
        setgroups = _read_bounded(setgroups_path).strip()
    except SandboxRuntimeError:
        # Kernels may omit setgroups for processes outside a user namespace.
        if not setgroups_path.exists():
            setgroups = None
        else:
            raise
    if setgroups not in (None, "allow", "deny"):
        raise SandboxRuntimeError("user namespace setgroups state is malformed")
    second_start = _parse_start_time(_read_bounded(process / "stat"))
    if first_start != second_start:
        raise SandboxRuntimeError("process identity changed during user namespace observation")

    return LinuxUserNamespaceSnapshot(
        pid=pid,
        start_time_ticks=first_start,
        uid_map=uid_map,
        gid_map=gid_map,
        setgroups=setgroups,
        observed_sources=("procfs:uid_map", "procfs:gid_map", "procfs:setgroups"),
        unknown_controls=(
            "procfs root and process identity are not independently attested",
            "mapping does not prove the process lacks host-visible privileges",
            "effective capabilities and parent user-namespace relationships require separate checks",
            "setgroups policy does not prove all privilege transitions are blocked",
            "mapping may change only through namespace lifecycle, but process state can change after observation",
            "mount, network, seccomp, LSM, cgroup, device, and secret exposure remain unverified",
        ),
        evidence_class="diagnostic_only",
        attested=False,
        verified=False,
    )
