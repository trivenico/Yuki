"""Read-only Linux cgroup v2 mount and process-membership verification.

The result is a point-in-time observation, not a race-free execution gate.
A supervisor must pin directory descriptors/mount identity and place the
process before exec to close the verification-to-execution race.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re

_MAX_FILE = 64 * 1024
_PID = re.compile(r"^[1-9][0-9]{0,9}$")
_MOUNT_ESCAPE = re.compile(r"\\([0-7]{3})")


@dataclass(frozen=True, slots=True)
class CgroupV2MembershipCheck:
    verified: bool
    pid: int
    process_start_time: int | None
    mount_id: int | None
    mount_point: str
    cgroup_path: str
    reason: str


def _read_bounded(path: Path) -> str:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags)
    try:
        chunks = bytearray()
        while len(chunks) <= _MAX_FILE:
            part = os.read(fd, min(4096, _MAX_FILE + 1 - len(chunks)))
            if not part:
                break
            chunks.extend(part)
        if len(chunks) > _MAX_FILE:
            raise ValueError("proc control file too large")
        return chunks.decode("utf-8", errors="strict")
    finally:
        os.close(fd)


def _unescape_mount_field(value: str) -> str:
    allowed = {"040": " ", "011": "\\t", "012": "\\n", "134": "\\\\"}
    parts: list[str] = []
    cursor = 0
    for match in _MOUNT_ESCAPE.finditer(value):
        literal = value[cursor:match.start()]
        if "\\\\" in literal:
            raise ValueError("malformed mountinfo escape")
        escaped = match.group(1)
        if escaped not in allowed:
            raise ValueError("unsupported mountinfo escape")
        parts.extend((literal, allowed[escaped]))
        cursor = match.end()
    tail = value[cursor:]
    if "\\\\" in tail or "\\x00" in value:
        raise ValueError("malformed mountinfo escape")
    parts.append(tail)
    return "".join(parts)

def parse_cgroup2_mountinfo(content: str) -> tuple[tuple[int, str, str, str], ...]:
    """Return (mount_id, root, mountpoint, device) for cgroup2 entries."""
    if type(content) is not str or len(content) > _MAX_FILE:
        raise ValueError("invalid mountinfo input")
    entries = []
    for line in content.splitlines():
        if not line:
            continue
        left_right = line.split(" - ")
        if len(left_right) != 2:
            raise ValueError("malformed mountinfo separator")
        left, right = left_right
        fields = left.split()
        tail = right.split()
        if len(fields) < 6 or len(tail) < 3:
            raise ValueError("truncated mountinfo record")
        if tail[0] != "cgroup2":
            continue
        if (
            not re.fullmatch(r"[1-9][0-9]*", fields[0])
            or not re.fullmatch(r"[1-9][0-9]*", fields[1])
            or not re.fullmatch(r"[0-9]+:[0-9]+", fields[2])
        ):
            raise ValueError("malformed mount identity")
        mount_id = int(fields[0])
        root = _unescape_mount_field(fields[3])
        mountpoint = _unescape_mount_field(fields[4])
        if not root.startswith("/") or not mountpoint.startswith("/"):
            raise ValueError("invalid cgroup2 mount paths")
        entries.append((mount_id, root, mountpoint, fields[2]))
    return tuple(entries)


def _process_start_time(stat_text: str) -> int:
    # comm is parenthesized but may itself contain spaces or ')'.
    close = stat_text.rfind(")")
    if close < 0:
        raise ValueError("malformed proc stat")
    fields = stat_text[close + 1:].strip().split()
    # fields[0] is field 3 (state); starttime is field 22.
    if len(fields) <= 19 or not re.fullmatch(r"[1-9][0-9]*", fields[19]):
        raise ValueError("missing or malformed process start time")
    return int(fields[19])


def _unified_cgroup_path(content: str) -> str:
    matches = []
    for line in content.splitlines():
        fields = line.split(":", 2)
        if len(fields) == 3 and fields[0] == "0" and fields[1] == "":
            path = fields[2]
            if not path.startswith("/") or "\x00" in path:
                raise ValueError("invalid unified cgroup path")
            if path != "/":
                components = path.split("/")[1:]
                if any(component in ("", ".", "..") for component in components):
                    raise ValueError("non-canonical unified cgroup path")
            matches.append(path)
    if len(matches) != 1:
        raise ValueError("expected exactly one unified cgroup entry")
    return matches[0]


def verify_process_cgroup_membership(
    pid: int,
    *,
    expected_cgroup_path: str,
    proc_root: str | Path = "/proc",
    mountinfo_path: str | Path | None = None,
) -> CgroupV2MembershipCheck:
    """Verify PID identity, cgroup2 mount mapping, and cgroup.procs membership."""
    result = CgroupV2MembershipCheck(
        False, pid if type(pid) is int else -1, None, None, "", "",
        "unavailable_or_malformed",
    )
    try:
        if type(pid) is not int or not _PID.fullmatch(str(pid)):
            raise ValueError("invalid PID")
        if type(expected_cgroup_path) is not str or not expected_cgroup_path.startswith("/"):
            raise ValueError("expected cgroup path must be absolute")
        if "\\x00" in expected_cgroup_path:
            raise ValueError("invalid expected cgroup path")
        if expected_cgroup_path != "/" and any(
            part in ("", ".", "..") for part in expected_cgroup_path.split("/")[1:]
        ):
            raise ValueError("non-canonical expected cgroup path")

        proc = Path(proc_root)
        process_dir = proc / str(pid)
        start_before = _process_start_time(_read_bounded(process_dir / "stat"))
        actual_cgroup = _unified_cgroup_path(_read_bounded(process_dir / "cgroup"))
        info_path = Path(mountinfo_path) if mountinfo_path else process_dir / "mountinfo"
        mounts = parse_cgroup2_mountinfo(_read_bounded(info_path))
        matching = []
        for mount_id, mount_root, mount_point, device in mounts:
            root_path = Path(mount_root)
            actual_path = Path(actual_cgroup)
            try:
                relative = actual_path.relative_to(root_path)
            except ValueError:
                continue
            matching.append((mount_id, Path(mount_point), relative, device))
        if len(matching) != 1:
            raise ValueError("unified cgroup path does not map to one cgroup2 mount")
        mount_id, mount_point, relative, _device = matching[0]
        resolved_mount = mount_point.resolve(strict=True)
        resolved_expected = (resolved_mount / relative).resolve(strict=True)
        expected = Path(expected_cgroup_path).resolve(strict=True)
        if resolved_expected != expected or not expected.is_dir():
            raise ValueError("process cgroup path differs from expected directory")

        procs = _read_bounded(expected / "cgroup.procs").split()
        if any(not _PID.fullmatch(item) for item in procs):
            raise ValueError("malformed cgroup.procs")
        if str(pid) not in procs:
            raise ValueError("PID absent from cgroup.procs")
        start_after = _process_start_time(_read_bounded(process_dir / "stat"))
        cgroup_after = _unified_cgroup_path(_read_bounded(process_dir / "cgroup"))
        procs_after = _read_bounded(expected / "cgroup.procs").split()
        if start_before != start_after:
            raise ValueError("PID identity changed during inspection")
        if cgroup_after != actual_cgroup or str(pid) not in procs_after:
            raise ValueError("process membership changed during inspection")
        return CgroupV2MembershipCheck(
            True, pid, start_after, mount_id, str(resolved_mount),
            actual_cgroup, "membership_verified_snapshot",
        )
    except (OSError, UnicodeError, ValueError, TypeError, RuntimeError, RecursionError):
        return result
