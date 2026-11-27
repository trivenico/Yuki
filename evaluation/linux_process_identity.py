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
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            return False
        if observed != self.start_time_ticks:
            return False
        # Recheck the pinned kernel handle after procfs inspection to catch exit
        # during the read. A numeric PID by itself is never treated as identity.
        return not poller.poll(0)

    def close(self) -> None:
        if self.pidfd >= 0:
            os.close(self.pidfd)
            self.pidfd = -1

    def __enter__(self) -> "PinnedLinuxProcess":
        self.fileno()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
