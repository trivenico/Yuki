"""Fail-closed parent-side UID/GID map writer for a blocked user-namespace child.

The child must already have successfully unshared CLONE_NEWUSER and remain
blocked in trusted bootstrap code. This module only writes the three procfs
mapping controls; it does not create namespaces or execute candidate code.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import re

from evaluation.linux_process_identity import PinnedLinuxProcess

_MAX_MAP_TEXT = 128


@dataclass(frozen=True, slots=True)
class LinuxIdMapRange:
    """One contiguous namespace-ID to parent-ID mapping."""

    inside_id: int
    outside_id: int
    length: int = 1

    def verify(self) -> bool:
        values = (self.inside_id, self.outside_id, self.length)
        return (
            all(type(value) is int for value in values)
            and 0 <= self.inside_id <= 2**31 - 1
            and 0 <= self.outside_id <= 2**31 - 1
            and 1 <= self.length <= 2**31 - 1
            and self.inside_id + self.length <= 2**31
            and self.outside_id + self.length <= 2**31
        )

    def render(self) -> str:
        if not self.verify():
            raise ValueError("ID mapping range is invalid")
        return f"{self.inside_id} {self.outside_id} {self.length}\n"


def _open_flags() -> int:
    flags = os.O_WRONLY
    for name in ("O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK"):
        flag = getattr(os, name, 0)
        if not flag:
            raise OSError("required Linux open flag is unavailable: " + name)
        flags |= flag
    return flags


def _write_proc_control(process_directory_fd: int, name: str, value: str) -> None:
    if name not in {"setgroups", "uid_map", "gid_map"}:
        raise ValueError("proc mapping control is not allowlisted")
    if type(value) is not str or not value.endswith("\n") or len(value) > _MAX_MAP_TEXT:
        raise ValueError("mapping control value is invalid")
    descriptor = os.open(name, _open_flags(), dir_fd=process_directory_fd)
    try:
        offset = 0
        encoded = value.encode("ascii", errors="strict")
        while offset < len(encoded):
            written = os.write(descriptor, encoded[offset:])
            if written <= 0:
                raise OSError("short write to proc mapping control")
            offset += written
    finally:
        os.close(descriptor)


def install_linux_user_namespace_maps(
    process: PinnedLinuxProcess,
    *,
    uid_map: LinuxIdMapRange,
    gid_map: LinuxIdMapRange,
    deny_setgroups: bool = True,
) -> None:
    """Install one-range UID/GID maps for a blocked child in a new user namespace.

    Mapping IDs must be explicitly supplied by the trusted supervisor. For an
    unprivileged single-ID mapping, outside IDs normally match the supervisor's
    effective UID/GID. The caller must verify policy and retain the child gate
    closed until independent namespace/credential read-back succeeds.
    """
    if not isinstance(process, PinnedLinuxProcess) or not process.verify():
        raise ProcessLookupError("user-namespace child identity is not live")
    if not isinstance(uid_map, LinuxIdMapRange) or not uid_map.verify():
        raise ValueError("UID mapping is invalid")
    if not isinstance(gid_map, LinuxIdMapRange) or not gid_map.verify():
        raise ValueError("GID mapping is invalid")
    if type(deny_setgroups) is not bool or not deny_setgroups:
        raise ValueError("setgroups must be explicitly denied for this bootstrap")

    flags = os.O_RDONLY
    for name in ("O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK", "O_DIRECTORY"):
        flag = getattr(os, name, 0)
        if not flag:
            raise OSError("required Linux open flag is unavailable: " + name)
        flags |= flag
    proc_root = os.open("/proc", flags)
    process_fd = -1
    try:
        process_fd = os.open(str(process.pid), flags, dir_fd=proc_root)
        if not process.verify():
            raise ProcessLookupError("user-namespace child exited before mapping")
        # Kernel requires setgroups=deny before gid_map for unprivileged callers.
        _write_proc_control(process_fd, "setgroups", "deny\n")
        _write_proc_control(process_fd, "uid_map", uid_map.render())
        _write_proc_control(process_fd, "gid_map", gid_map.render())
        if not process.verify():
            raise ProcessLookupError("user-namespace child exited during mapping")
    finally:
        for descriptor in (process_fd, proc_root):
            if descriptor >= 0:
                os.close(descriptor)
