"""Descriptor-pinned, read-only access to a cgroup v2 directory.

The caller must supply an already trusted and pinned cgroup mount-root fd.
This module walks relative components with openat/O_NOFOLLOW and reads only
allowlisted control files through the resulting directory descriptor. It does
not create cgroups, write controls, move processes, or establish a sandbox.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import re
import stat

_MAX_CONTROL_BYTES = 64 * 1024
_ALLOWED_FILES = frozenset({
    "cgroup.procs",
    "cpu.max",
    "memory.max",
    "memory.swap.max",
    "pids.max",
})
_COMPONENT = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_PID = re.compile(r"^[1-9][0-9]{0,9}$")


def _mount_id(descriptor: int) -> int:
    """Read Linux's stable mount ID for an open descriptor; fail closed without procfs."""
    path = f"/proc/self/fdinfo/{descriptor}"
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    try:
        data = os.read(fd, 16 * 1024 + 1)
        if len(data) > 16 * 1024:
            raise ValueError("descriptor fdinfo exceeds size limit")
        text = data.decode("ascii", errors="strict")
    finally:
        os.close(fd)
    values = [line.split(":", 1)[1].strip() for line in text.splitlines()
              if line.startswith("mnt_id:")]
    if len(values) != 1 or not values[0].isdigit():
        raise ValueError("descriptor mount identity is unavailable")
    return int(values[0])


@dataclass(slots=True)
class PinnedCgroupDirectory:
    """Owned descriptor for one cgroup directory, anchored to a trusted root."""

    descriptor: int
    relative_path: str
    device: int
    inode: int

    def fileno(self) -> int:
        if self.descriptor < 0:
            raise ValueError("cgroup directory descriptor is closed")
        info = os.fstat(self.descriptor)
        if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != (
            self.device, self.inode
        ):
            raise ValueError("pinned cgroup directory identity changed")
        return self.descriptor

    def close(self) -> None:
        if self.descriptor >= 0:
            os.close(self.descriptor)
            self.descriptor = -1

    def __enter__(self) -> "PinnedCgroupDirectory":
        self.fileno()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


def open_pinned_cgroup_directory(
    trusted_mount_root_fd: int, relative_path: str
) -> PinnedCgroupDirectory:
    """Open a descendant without following symlinks or crossing the root mount.

    The root descriptor must already identify the intended cgroup v2 mount
    root. Each opened component is checked against its Linux mount ID as well
    as its device, preventing same-device bind mounts from escaping the mount
    boundary. Missing procfs mount identity fails closed. An empty path is
    rejected so this API cannot accidentally treat the delegated root itself
    as a per-attempt cgroup.
    """
    if type(trusted_mount_root_fd) is not int or trusted_mount_root_fd < 0:
        raise ValueError("trusted mount-root descriptor is invalid")
    if type(relative_path) is not str or not relative_path or len(relative_path) > 1024:
        raise ValueError("relative cgroup path is invalid")
    if relative_path.startswith("/") or "\x00" in relative_path or "\\" in relative_path:
        raise ValueError("cgroup path must be relative and canonical")
    parts = relative_path.split("/")
    if not parts or any(
        part in ("", ".", "..") or not _COMPONENT.fullmatch(part)
        for part in parts
    ):
        raise ValueError("cgroup path contains an invalid component")

    root_info = os.fstat(trusted_mount_root_fd)
    if not stat.S_ISDIR(root_info.st_mode):
        raise ValueError("trusted mount-root descriptor is not a directory")
    root_mount_id = _mount_id(trusted_mount_root_fd)

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    current_fd = os.dup(trusted_mount_root_fd)
    try:
        for part in parts:
            next_fd = os.open(part, flags, dir_fd=current_fd)
            try:
                info = os.fstat(next_fd)
                if not stat.S_ISDIR(info.st_mode) or info.st_dev != root_info.st_dev:
                    raise ValueError("cgroup component is not on the pinned filesystem")
                if _mount_id(next_fd) != root_mount_id:
                    raise ValueError("cgroup component crosses the pinned mount boundary")
            except Exception:
                os.close(next_fd)
                raise
            os.close(current_fd)
            current_fd = next_fd
        final_info = os.fstat(current_fd)
        return PinnedCgroupDirectory(
            current_fd, relative_path, final_info.st_dev, final_info.st_ino
        )
    except Exception:
        os.close(current_fd)
        raise


def read_pinned_cgroup_control(
    directory: PinnedCgroupDirectory, filename: str
) -> str:
    """Read one bounded allowlisted control file relative to the pinned fd."""
    if not isinstance(directory, PinnedCgroupDirectory):
        raise ValueError("pinned cgroup directory is required")
    if type(filename) is not str or filename not in _ALLOWED_FILES:
        raise ValueError("cgroup control filename is not allowlisted")
    directory_fd = directory.fileno()
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(filename, flags, dir_fd=directory_fd)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_dev != directory.device:
            raise ValueError("cgroup control is not a regular file on the pinned filesystem")
        if _mount_id(descriptor) != _mount_id(directory_fd):
            raise ValueError("cgroup control crosses the pinned mount boundary")
        data = bytearray()
        while len(data) <= _MAX_CONTROL_BYTES:
            chunk = os.read(
                descriptor, min(4096, _MAX_CONTROL_BYTES + 1 - len(data))
            )
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > _MAX_CONTROL_BYTES:
            raise ValueError("cgroup control file exceeds size limit")
        return data.decode("ascii", errors="strict").strip()
    finally:
        os.close(descriptor)


def pinned_cgroup_contains_pid(
    directory: PinnedCgroupDirectory, pid: int
) -> bool:
    """Return a point-in-time membership observation from pinned cgroup.procs."""
    if type(pid) is not int or not _PID.fullmatch(str(pid)):
        raise ValueError("PID must be a positive bounded integer")
    contents = read_pinned_cgroup_control(directory, "cgroup.procs")
    entries = contents.split()
    if any(not _PID.fullmatch(entry) for entry in entries):
        raise ValueError("cgroup.procs contains malformed PID data")
    return str(pid) in entries
