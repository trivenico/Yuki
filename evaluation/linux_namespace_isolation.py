"""Read-back verification primitives for a future Linux isolation adapter.

This module observes kernel state for a blocked process. It does not create
namespaces, change credentials, drop capabilities, or enforce isolation.
Callers must compare the child snapshot with a baseline captured before fork
and must keep the child blocked throughout inspection.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import re
import stat
from typing import Mapping

from evaluation.linux_process_identity import PinnedLinuxProcess

_NAMESPACE_NAMES = ("cgroup", "ipc", "mnt", "net", "pid", "user", "uts")
_STATUS_FIELDS = frozenset({"Uid", "Gid", "CapEff", "NoNewPrivs"})
_MAX_STATUS_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class LinuxNamespaceBaseline:
    """Namespace inode identities observed in the supervising process."""

    inodes: Mapping[str, int]

    def verify(self) -> bool:
        return (
            isinstance(self.inodes, Mapping)
            and set(self.inodes) == set(_NAMESPACE_NAMES)
            and all(type(value) is int and value > 0 for value in self.inodes.values())
        )


@dataclass(frozen=True, slots=True)
class LinuxIsolationSnapshot:
    """Point-in-time credentials, capabilities, and namespace identities."""

    uid: tuple[int, int, int, int]
    gid: tuple[int, int, int, int]
    effective_capabilities: int
    no_new_privileges: bool
    namespace_inodes: Mapping[str, int]


def _namespace_inodes(namespace_directory_fd: int) -> dict[str, int]:
    observed = {}
    for name in _NAMESPACE_NAMES:
        info = os.stat(name, dir_fd=namespace_directory_fd, follow_symlinks=True)
        if not stat.S_ISREG(info.st_mode) and not stat.S_ISDIR(info.st_mode):
            # procfs namespace magic links resolve to namespace objects whose
            # stat mode varies by kernel; inode identity is the useful value.
            if info.st_ino <= 0:
                raise ValueError("namespace inode is invalid")
        if info.st_ino <= 0:
            raise ValueError("namespace inode is invalid")
        observed[name] = info.st_ino
    return observed


def capture_linux_namespace_baseline() -> LinuxNamespaceBaseline:
    """Capture this process's namespace inode identities as a comparison base."""
    flags = os.O_RDONLY
    for name in ("O_CLOEXEC", "O_DIRECTORY"):
        flag = getattr(os, name, 0)
        if not flag:
            raise OSError("required Linux open flag is unavailable: " + name)
        flags |= flag
    namespace_fd = os.open("/proc/self/ns", flags)
    try:
        baseline = LinuxNamespaceBaseline(_namespace_inodes(namespace_fd))
    finally:
        os.close(namespace_fd)
    if not baseline.verify():
        raise RuntimeError("could not capture a complete namespace baseline")
    return baseline


def _read_status(process_directory_fd: int) -> dict[str, str]:
    flags = os.O_RDONLY
    for name in ("O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK"):
        flag = getattr(os, name, 0)
        if not flag:
            raise OSError("required Linux open flag is unavailable: " + name)
        flags |= flag
    descriptor = os.open("status", flags, dir_fd=process_directory_fd)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("process status is not a regular procfs file")
        data = bytearray()
        while len(data) <= _MAX_STATUS_BYTES:
            chunk = os.read(descriptor, min(4096, _MAX_STATUS_BYTES + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > _MAX_STATUS_BYTES:
            raise ValueError("process status exceeds size limit")
    finally:
        os.close(descriptor)

    fields = {}
    for line in data.decode("ascii", errors="strict").splitlines():
        key, separator, value = line.partition(":")
        if key in _STATUS_FIELDS:
            if not separator or key in fields:
                raise ValueError("process status contains malformed isolation fields")
            fields[key] = value.strip()
    if set(fields) != _STATUS_FIELDS:
        raise ValueError("process status is missing required isolation fields")
    return fields


def _parse_id_quad(value: str, field: str) -> tuple[int, int, int, int]:
    parts = value.split()
    if len(parts) != 4 or any(not re.fullmatch(r"[0-9]{1,10}", item) for item in parts):
        raise ValueError(field + " status field is malformed")
    return tuple(int(item) for item in parts)  # type: ignore[return-value]


def inspect_linux_process_isolation(
    process: PinnedLinuxProcess,
) -> LinuxIsolationSnapshot:
    """Read one identity-pinned /proc snapshot; caller must keep target blocked."""
    if not isinstance(process, PinnedLinuxProcess) or not process.verify():
        raise ProcessLookupError("target process identity is not live")
    flags = os.O_RDONLY
    for name in ("O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK", "O_DIRECTORY"):
        flag = getattr(os, name, 0)
        if not flag:
            raise OSError("required Linux open flag is unavailable: " + name)
        flags |= flag
    proc_root = os.open("/proc", flags)
    process_fd = -1
    namespace_fd = -1
    try:
        process_fd = os.open(str(process.pid), flags, dir_fd=proc_root)
        namespace_fd = os.open("ns", flags, dir_fd=process_fd)
        status = _read_status(process_fd)
        namespaces = _namespace_inodes(namespace_fd)
        snapshot = LinuxIsolationSnapshot(
            uid=_parse_id_quad(status["Uid"], "Uid"),
            gid=_parse_id_quad(status["Gid"], "Gid"),
            effective_capabilities=int(status["CapEff"], 16),
            no_new_privileges=status["NoNewPrivs"] == "1",
            namespace_inodes=namespaces,
        )
        if not process.verify():
            raise ProcessLookupError("target process exited during isolation inspection")
        return snapshot
    finally:
        for descriptor in (namespace_fd, process_fd, proc_root):
            if descriptor >= 0:
                os.close(descriptor)


def verify_linux_process_isolation(
    snapshot: LinuxIsolationSnapshot,
    baseline: LinuxNamespaceBaseline,
    *,
    expected_uid: int,
    expected_gid: int,
    required_distinct_namespaces: tuple[str, ...] = ("ipc", "mnt", "net", "uts", "user"),
) -> bool:
    """Require explicit credentials, zero effective caps, NNP, and namespace separation."""
    if not isinstance(snapshot, LinuxIsolationSnapshot) or not isinstance(
        baseline, LinuxNamespaceBaseline
    ) or not baseline.verify():
        return False
    if type(expected_uid) is not int or expected_uid < 0:
        return False
    if type(expected_gid) is not int or expected_gid < 0:
        return False
    if any(type(name) is not str or name not in _NAMESPACE_NAMES
           for name in required_distinct_namespaces):
        return False
    if len(set(required_distinct_namespaces)) != len(required_distinct_namespaces):
        return False
    if snapshot.uid != (expected_uid,) * 4 or snapshot.gid != (expected_gid,) * 4:
        return False
    if snapshot.effective_capabilities != 0 or snapshot.no_new_privileges is not True:
        return False
    if set(snapshot.namespace_inodes) != set(_NAMESPACE_NAMES):
        return False
    if any(type(value) is not int or value <= 0
           for value in snapshot.namespace_inodes.values()):
        return False
    return all(
        snapshot.namespace_inodes[name] != baseline.inodes[name]
        for name in required_distinct_namespaces
    )
