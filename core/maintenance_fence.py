"""Cross-process maintenance fence for private Yuki control-plane state.

The first implementation deliberately serializes all participating operations
with an exclusive OS lock. This sacrifices read/write concurrency in exchange
for a smaller, easier-to-audit locking contract. Callers must acquire the lease
before opening SQLite connections or beginning transactions.
"""
from __future__ import annotations

from contextlib import contextmanager
import math
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
_active_fds: set[int] = set()


def _after_fork_in_child() -> None:
    """Drop inherited descriptors and reset thread primitives in a forked child."""
    global _registry_guard, _registry, _active_fds
    for descriptor in tuple(_active_fds):
        try:
            os.close(descriptor)
        except OSError:
            pass
    _active_fds = set()
    _registry_guard = threading.Lock()
    _registry = {}
    _thread_state.leases = {}


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork_in_child)


def _validate_timeout(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("timeout_seconds must be a finite non-negative number.")
    # Compare before float conversion: converting an arbitrarily large int can
    # raise OverflowError, while the bounded range check rejects it cleanly.
    if value < 0 or value > 3600:
        raise ValueError("timeout_seconds must be between 0 and 3600.")
    try:
        normalized = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError("timeout_seconds must be a finite non-negative number.") from exc
    if not math.isfinite(normalized):
        raise ValueError("timeout_seconds must be a finite non-negative number.")
    return normalized


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
        path_info = os.stat(path, follow_symlinks=False)
        opened_info = os.fstat(fd)
        if (opened_info.st_dev, opened_info.st_ino) != (path_info.st_dev, path_info.st_ino):
            os.close(fd)
            raise MaintenanceFenceError("maintenance_lock_path_changed")
        _active_fds.add(fd)
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
        self.timeout_seconds = _validate_timeout(timeout_seconds)
        self.profile_root = Path(profile_root)
        self.lock_path = self.profile_root / "maintenance.lock"

    @contextmanager
    def lease(self, *, timeout_seconds: float | None = None) -> Iterator[None]:
        timeout = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        timeout = _validate_timeout(timeout)
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
                _active_fds.discard(fd)
            thread_lock.release()
