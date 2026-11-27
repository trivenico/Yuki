"""pidfd-backed Linux process identity snapshots.

This module pins a process reference with pidfd_open and binds it to the
kernel-reported /proc start time. It is an identity primitive only: it does
not launch, stop, sandbox, authorize, or inspect the file descriptors of a
process. Callers must still treat verification as a point-in-time check.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import select
import stat


_MAX_STAT_BYTES = 4096


def _read_start_time(pid: int) -> int:
    """Read field 22 from /proc/<pid>/stat without following a final symlink."""
    if type(pid) is not int or pid <= 0:
        raise ValueError("pid must be a positive integer")
    flags = os.O_RDONLY
    for name in ("O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK"):
        flag = getattr(os, name, 0)
        if not flag:
            raise OSError("required Linux open flag is unavailable: " + name)
        flags |= flag
    fd = os.open(f"/proc/{pid}/stat", flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("process stat is not a regular procfs file")
        data = bytearray()
        while len(data) <= _MAX_STAT_BYTES:
            chunk = os.read(fd, min(1024, _MAX_STAT_BYTES + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > _MAX_STAT_BYTES:
            raise ValueError("process stat exceeds size limit")
    finally:
        os.close(fd)

    text = data.decode("ascii", errors="strict")
    # comm is parenthesized but may itself contain spaces and ')'. Split only
    # after its final closing parenthesis; the remainder begins at field 3.
    close = text.rfind(")")
    if close < 0 or close + 2 >= len(text):
        raise ValueError("process stat is malformed")
    prefix = text[:close]
    if not prefix.startswith(str(pid) + " ("):
        raise ProcessLookupError("procfs PID does not match requested PID")
    fields = text[close + 1 :].split()
    # starttime is field 22; fields[0] is field 3 (state).
    if len(fields) <= 19 or len(fields[0]) != 1:
        raise ValueError("process stat is missing required fields")
    token = fields[19]
    if not token.isascii() or not token.isdecimal():
        raise ValueError("process start time is malformed")
    return int(token)


def _read_start_time_at(process_directory_fd: int, pid: int) -> int:
    """Read start time through an already-pinned /proc/<pid> directory."""
    flags = os.O_RDONLY
    for name in ("O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK"):
        flag = getattr(os, name, 0)
        if not flag:
            raise OSError("required Linux open flag is unavailable: " + name)
        flags |= flag
    descriptor = os.open("stat", flags, dir_fd=process_directory_fd)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("process stat is not a regular procfs file")
        data = bytearray()
        while len(data) <= _MAX_STAT_BYTES:
            chunk = os.read(descriptor, min(1024, _MAX_STAT_BYTES + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > _MAX_STAT_BYTES:
            raise ValueError("process stat exceeds size limit")
    finally:
        os.close(descriptor)
    text = data.decode("ascii", errors="strict")
    close = text.rfind(")")
    if close < 0 or close + 2 >= len(text):
        raise ValueError("process stat is malformed")
    if not text[:close].startswith(str(pid) + " ("):
        raise ProcessLookupError("procfs PID does not match pinned process")
    fields = text[close + 1 :].split()
    if len(fields) <= 19 or len(fields[0]) != 1:
        raise ValueError("process stat is missing required fields")
    token = fields[19]
    if not token.isascii() or not token.isdecimal():
        raise ValueError("process start time is malformed")
    return int(token)


@dataclass(slots=True)
class PinnedLinuxProcess:
    """Owned pidfd plus the PID/start-time identity captured at acquisition."""

    pid: int
    start_time_ticks: int
    pidfd: int

    @classmethod
    def open(cls, pid: int) -> "PinnedLinuxProcess":
        """Acquire pidfd first, then bind it to a live procfs start-time value."""
        if type(pid) is not int or pid <= 0:
            raise ValueError("pid must be a positive integer")
        opener = getattr(os, "pidfd_open", None)
        if opener is None:
            raise RuntimeError("Linux pidfd_open is unavailable")
        descriptor = opener(pid, 0)
        try:
            start_time = _read_start_time(pid)
            instance = cls(pid=pid, start_time_ticks=start_time, pidfd=descriptor)
            if not instance.verify():
                raise ProcessLookupError("process exited during identity acquisition")
            return instance
        except BaseException:
            os.close(descriptor)
            raise

    def fileno(self) -> int:
        if self.pidfd < 0:
            raise ValueError("process pidfd is closed")
        os.fstat(self.pidfd)
        return self.pidfd

    def verify(self) -> bool:
        """Return true only while pidfd and procfs still identify this process."""
        descriptor = self.fileno()
        poller = select.poll()
        poller.register(descriptor, select.POLLIN | select.POLLHUP | select.POLLERR)
        if poller.poll(0):
            return False
        try:
            observed = _read_start_time(self.pid)
        except (OSError, ValueError, UnicodeError):
            # Missing, inaccessible, replaced, or malformed procfs identity
            # data must never be interpreted as a successful verification.
            return False
        if observed != self.start_time_ticks:
            return False
        # Recheck the pinned kernel handle after procfs inspection to catch exit
        # during the read. A numeric PID by itself is never treated as identity.
        return not poller.poll(0)

    def open_file_descriptors(self) -> tuple[int, ...]:
        """Return one bounded snapshot of the process's numeric open-FD set.

        The procfs PID directory and fd directory are opened without following
        final symlinks. The pidfd and start-time identity are checked around
        enumeration. This is a snapshot, not a lock: callers must keep the
        target blocked and prevent it from changing descriptors during checks.
        """
        self.fileno()
        flags = os.O_RDONLY
        for name in ("O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK", "O_DIRECTORY"):
            flag = getattr(os, name, 0)
            if not flag:
                raise OSError("required Linux open flag is unavailable: " + name)
            flags |= flag
        proc_root = os.open("/proc", flags)
        process_directory = -1
        fd_directory = -1
        try:
            process_directory = os.open(str(self.pid), flags, dir_fd=proc_root)
            if _read_start_time_at(process_directory, self.pid) != self.start_time_ticks:
                raise ProcessLookupError("process identity changed before descriptor inspection")
            fd_directory = os.open("fd", flags, dir_fd=process_directory)
            names = os.listdir(fd_directory)
            if len(names) > 65536:
                raise ValueError("process descriptor count exceeds safety limit")
            descriptors = []
            for name in names:
                if not name.isascii() or not name.isdecimal():
                    raise ValueError("proc fd directory contains an invalid entry")
                descriptors.append(int(name))
            if len(set(descriptors)) != len(descriptors):
                raise ValueError("proc fd directory contains duplicate entries")
            if _read_start_time_at(process_directory, self.pid) != self.start_time_ticks:
                raise ProcessLookupError("process identity changed during descriptor inspection")
            if not self.verify():
                raise ProcessLookupError("process exited during descriptor inspection")
            return tuple(sorted(descriptors))
        finally:
            for descriptor in (fd_directory, process_directory, proc_root):
                if descriptor >= 0:
                    os.close(descriptor)

    def verify_descriptor_allowlist(self, allowed_fds: tuple[int, ...] | list[int]) -> bool:
        """Require the observed FD numbers to equal an explicit allowlist."""
        if not isinstance(allowed_fds, (tuple, list)):
            raise TypeError("allowed_fds must be a tuple or list of integers")
        if any(type(fd) is not int or fd < 0 for fd in allowed_fds):
            raise ValueError("allowed descriptor numbers must be non-negative integers")
        if len(set(allowed_fds)) != len(allowed_fds):
            raise ValueError("allowed descriptor numbers must be unique")
        return self.open_file_descriptors() == tuple(sorted(allowed_fds))

    def close(self) -> None:
        if self.pidfd >= 0:
            os.close(self.pidfd)
            self.pidfd = -1

    def __enter__(self) -> "PinnedLinuxProcess":
        self.fileno()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
