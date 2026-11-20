"""Cross-process locks and durable atomic JSON writes for local Yuki stores.

Atomic replacement prevents partial files; the adjacent lock coordinates the
entire read-modify-write transaction across threads and processes. This module
does not provide encryption and assumes the parent directory is private.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
import tempfile
from threading import RLock
from typing import Any

_THREAD_LOCKS: dict[str, RLock] = {}
_THREAD_LOCKS_GUARD = RLock()


def _thread_lock(path: Path) -> RLock:
    key = str(path.resolve())
    with _THREAD_LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(key, RLock())


@contextmanager
def file_lock(path: str | Path):
    """Exclusively lock a path using a sibling .lock file.

    POSIX uses flock; Windows uses a one-byte msvcrt region lock. The process-
    local lock also serializes multiple store instances in the same process.
    Lock files are regular, single-link files and owner-only on POSIX.
    """
    target = Path(path).expanduser()
    parent = target.parent.resolve()
    parent.mkdir(parents=True, exist_ok=True)
    lock_path = parent / (target.name + ".lock")
    thread_lock = _thread_lock(lock_path)
    with thread_lock:
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
        if os.name == "posix":
            flags |= getattr(os, "O_NOFOLLOW", 0)
        elif lock_path.is_symlink():
            raise RuntimeError("Local store lock path must not be a symbolic link.")
        try:
            descriptor = os.open(lock_path, flags, 0o600)
        except OSError as exc:
            raise RuntimeError("Could not open the local store lock.") from exc
        locked = False
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise RuntimeError("Local store lock must be a regular single-link file.")
            if os.name == "posix":
                os.fchmod(descriptor, 0o600)
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                locked = True
            else:
                import msvcrt
                if info.st_size == 0:
                    os.write(descriptor, b"0")
                    os.fsync(descriptor)
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
                locked = True
            yield
        finally:
            if locked:
                if os.name == "posix":
                    import fcntl
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                else:
                    import msvcrt
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            os.close(descriptor)


def atomic_write_json(path: str | Path, value: Any, *, indent: int = 2) -> None:
    """Durably replace a JSON file without exposing a partial write."""
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix="." + target.name + "-", suffix=".tmp", dir=str(target.parent)
    )
    try:
        if os.name == "posix":
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=indent)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, target)
        if os.name == "posix":
            directory_fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
