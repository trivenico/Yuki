"""Cross-process maintenance fence for private Yuki control-plane state.

The first implementation deliberately serializes all participating operations
with an exclusive OS lock. This sacrifices read/write concurrency in exchange
for a smaller, easier-to-audit locking contract. Callers must acquire the lease
before opening SQLite connections or beginning transactions.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import stat
import threading
import time
from typing import Iterator

from core.private_profile import PrivateProfileError, ensure_private_profile_directory


class MaintenanceFenceError(RuntimeError):
    """The maintenance fence could not safely be acquired."""


class MaintenanceBusyError(MaintenanceFenceError):
    """Another process currently owns the maintenance fence."""


_registry_guard = threading.Lock()
_registry: dict[str, threading.RLock] = {}
_thread_state = threading.local()


def _thread_lock(path: str) -> threading.RLock:
    with _registry_guard:
        lock = _registry.get(path)
        if lock is None:
            lock = threading.RLock()
            _registry[path] = lock
        return lock


def _open_lock_file(path: Path) -> int:
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    if os.name == "posix":
        if not hasattr(os, "O_NOFOLLOW"):
            raise MaintenanceFenceError("maintenance_nofollow_unavailable")
        flags |= os.O_NOFOLLOW
    elif os.name == "nt":
        if path.is_symlink():
            raise MaintenanceFenceError("maintenance_symlink_not_allowed")
    else:
        raise MaintenanceFenceError("maintenance_platform_unsupported")

    try:
        fd = os.open(path, flags, 0o600)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            os.close(fd)
            raise MaintenanceFenceError("maintenance_lock_file_unsafe")
        if os.name == "posix":
            if info.st_uid != os.geteuid():
                os.close(fd)
                raise MaintenanceFenceError("maintenance_lock_owner_mismatch")
            if stat.S_IMODE(info.st_mode) & 0o077:
                os.close(fd)
                raise MaintenanceFenceError("maintenance_lock_permissions_too_broad")
            os.fchmod(fd, 0o600)
        if os.fstat(fd).st_ino != os.stat(path, follow_symlinks=False).st_ino:
            os.close(fd)
            raise MaintenanceFenceError("maintenance_lock_path_changed")
        return fd
    except MaintenanceFenceError:
        raise
    except OSError as exc:
        raise MaintenanceFenceError("maintenance_lock_file_unavailable") from exc


def _try_os_lock(fd: int) -> bool:
    if os.name == "posix":
        import fcntl
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False
    if os.name == "nt":
        import msvcrt
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    raise MaintenanceFenceError("maintenance_platform_unsupported")


def _unlock_os(fd: int) -> None:
    if os.name == "posix":
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)
    elif os.name == "nt":
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)


class MaintenanceFence:
    """Exclusive cross-process lease rooted in an already-private profile."""

    def __init__(self, profile_root: str | Path, *, timeout_seconds: float = 10.0):
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
            raise ValueError("timeout_seconds must be a finite non-negative number.")
        if timeout_seconds < 0 or timeout_seconds > 3600:
            raise ValueError("timeout_seconds must be between 0 and 3600.")
        self.profile_root = Path(profile_root)
        self.timeout_seconds = float(timeout_seconds)
        self.lock_path = self.profile_root / "maintenance.lock"

    @contextmanager
    def lease(self, *, timeout_seconds: float | None = None) -> Iterator[None]:
        timeout = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout < 0 or timeout > 3600:
            raise ValueError("timeout_seconds must be between 0 and 3600.")
        try:
            root = ensure_private_profile_directory(self.profile_root)
        except PrivateProfileError as exc:
            raise MaintenanceFenceError("maintenance_profile_not_private") from exc

        lock_path = str(root / "maintenance.lock")
        thread_lock = _thread_lock(lock_path)
        acquired_thread = thread_lock.acquire(timeout=float(timeout))
        if not acquired_thread:
            raise MaintenanceBusyError("maintenance_fence_busy")

        states = getattr(_thread_state, "leases", None)
        if states is None:
            states = {}
            _thread_state.leases = states
        existing = states.get(lock_path)
        if existing is not None:
            existing["depth"] += 1
            try:
                yield
            finally:
                existing["depth"] -= 1
                thread_lock.release()
            return

        fd = None
        os_locked = False
        deadline = time.monotonic() + float(timeout)
        try:
            fd = _open_lock_file(Path(lock_path))
            while True:
                if _try_os_lock(fd):
                    os_locked = True
                    break
                if time.monotonic() >= deadline:
                    raise MaintenanceBusyError("maintenance_fence_busy")
                time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
            states[lock_path] = {"fd": fd, "depth": 1}
            yield
        finally:
            states.pop(lock_path, None)
            if fd is not None:
                if os_locked:
                    try:
                        _unlock_os(fd)
                    finally:
                        os.close(fd)
                else:
                    os.close(fd)
            thread_lock.release()
