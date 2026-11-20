"""Read-only Linux process observations for sandbox diagnostics, never attestation."""
from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from core.sandbox_runtime import SandboxRuntimeError

_MAX_PROC_FILE = 65_536
_REQUIRED_NAMESPACES = ("user", "pid", "mnt", "ipc", "net")
_NAMESPACE_RE = re.compile(r"^[A-Za-z0-9_-]+:\[[0-9]+\]$")
_UINT_RE = re.compile(r"^[0-9]+$")


@dataclass(frozen=True)
class LinuxProcessSnapshot:
    """Bounded observations from procfs; caller-supplied roots remain untrusted."""

    pid: int
    start_time_ticks: int
    namespace_links: tuple[tuple[str, str], ...]
    real_uid: int
    effective_uid: int
    real_gid: int
    effective_gid: int
    no_new_privileges: int
    seccomp_mode: int
    effective_capabilities_hex: str
    cgroup_v2_path: str | None
    observed_sources: tuple[str, ...]
    unknown_controls: tuple[str, ...]
    attested: bool = False


def _read_bounded(path: Path, limit: int = _MAX_PROC_FILE) -> str:
    try:
        with path.open("rb") as stream:
            raw = stream.read(limit + 1)
    except OSError as exc:
        raise SandboxRuntimeError("required procfs observation is unavailable") from exc
    if len(raw) > limit:
        raise SandboxRuntimeError("procfs observation exceeds its size limit")
    try:
        return raw.decode("ascii", errors="strict")
    except UnicodeError as exc:
        raise SandboxRuntimeError("procfs observation is not valid ASCII") from exc


def _parse_start_time(stat_text: str) -> int:
    # comm is parenthesized and may itself contain spaces or ')' characters.
    close = stat_text.rfind(")")
    if close < 0:
        raise SandboxRuntimeError("malformed procfs stat record")
    fields = stat_text[close + 1 :].strip().split()
    if len(fields) < 20 or not _UINT_RE.fullmatch(fields[19]):
        raise SandboxRuntimeError("procfs stat record has invalid start time")
    return int(fields[19])


def _parse_status(status_text: str) -> dict[str, str]:
    wanted = {"Uid", "Gid", "NoNewPrivs", "Seccomp", "CapEff"}
    found: dict[str, str] = {}
    for line in status_text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        if key in wanted:
            if key in found:
                raise SandboxRuntimeError("procfs status contains duplicate security fields")
            found[key] = value.strip()
    if found.keys() != wanted:
        raise SandboxRuntimeError("procfs status is missing required security fields")
    return found


def _parse_ids(value: str, label: str) -> tuple[int, int]:
    fields = value.split()
    if len(fields) < 2 or any(not _UINT_RE.fullmatch(item) for item in fields[:2]):
        raise SandboxRuntimeError("procfs " + label + " fields are malformed")
    return int(fields[0]), int(fields[1])


def _parse_cgroup_v2(payload: str) -> str | None:
    entries = [line for line in payload.splitlines() if line.startswith("0::")]
    if len(entries) > 1:
        raise SandboxRuntimeError("procfs cgroup has ambiguous v2 membership")
    if not entries:
        return None
    path = entries[0][3:]
    if not path.startswith("/") or "\x00" in path or any(part in (".", "..") for part in path.split("/")):
        raise SandboxRuntimeError("procfs cgroup path is unsafe")
    return path


def collect_linux_process_snapshot(pid: int, *, proc_root: str = "/proc") -> LinuxProcessSnapshot:
    """Read a bounded process snapshot twice to detect PID reuse during collection.

    This records only observations. It does not inspect the runtime policy, prove
    namespace isolation, verify cgroup limits, or authorize any workload.
    """
    if os.name != "posix":
        raise SandboxRuntimeError("Linux procfs observations require a POSIX host")
    if type(pid) is not int or pid <= 0:
        raise SandboxRuntimeError("process ID must be a positive integer")
    if type(proc_root) is not str or not proc_root or not os.path.isabs(proc_root):
        raise SandboxRuntimeError("procfs root must be an absolute path")
    root = Path(proc_root)
    try:
        root_info = root.lstat()
    except OSError as exc:
        raise SandboxRuntimeError("procfs root is unavailable") from exc
    if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
        raise SandboxRuntimeError("procfs root must be a non-symlink directory")

    process_dir = root / str(pid)
    first_start = _parse_start_time(_read_bounded(process_dir / "stat"))
    status = _parse_status(_read_bounded(process_dir / "status"))
    namespace_links: list[tuple[str, str]] = []
    for namespace in _REQUIRED_NAMESPACES:
        try:
            target = os.readlink(process_dir / "ns" / namespace)
        except OSError as exc:
            raise SandboxRuntimeError("required process namespace link is unavailable") from exc
        if not _NAMESPACE_RE.fullmatch(target):
            raise SandboxRuntimeError("process namespace link has an invalid identity")
        namespace_links.append((namespace, target))

    cgroup_path = _parse_cgroup_v2(_read_bounded(process_dir / "cgroup"))
    second_start = _parse_start_time(_read_bounded(process_dir / "stat"))
    if first_start != second_start:
        raise SandboxRuntimeError("process identity changed during observation")

    uid_real, uid_effective = _parse_ids(status["Uid"], "UID")
    gid_real, gid_effective = _parse_ids(status["Gid"], "GID")
    if status["NoNewPrivs"] not in ("0", "1") or status["Seccomp"] not in ("0", "1", "2"):
        raise SandboxRuntimeError("procfs privilege state is malformed")
    cap_eff = status["CapEff"]
    if not re.fullmatch(r"[0-9a-fA-F]{1,32}", cap_eff):
        raise SandboxRuntimeError("procfs effective capability mask is malformed")

    return LinuxProcessSnapshot(
        pid=pid,
        start_time_ticks=first_start,
        namespace_links=tuple(namespace_links),
        real_uid=uid_real,
        effective_uid=uid_effective,
        real_gid=gid_real,
        effective_gid=gid_effective,
        no_new_privileges=int(status["NoNewPrivs"]),
        seccomp_mode=int(status["Seccomp"]),
        effective_capabilities_hex=cap_eff.lower(),
        cgroup_v2_path=cgroup_path,
        observed_sources=("procfs:stat", "procfs:status", "procfs:ns", "procfs:cgroup"),
        unknown_controls=(
            "namespace isolation relative to trusted host baseline",
            "effective seccomp filter contents and syscall coverage",
            "mandatory access-control enforcement",
            "cgroup resource-limit enforcement",
            "mount and network isolation",
            "host secret exclusion",
            "runtime socket and device exposure",
            "process-tree cleanup and lifecycle",
        ),
        attested=False,
    )
