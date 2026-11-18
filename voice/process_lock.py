"""Cross-process locking for private per-user application directories."""
from __future__ import annotations

import ctypes
import os
import stat
import time
from contextlib import contextmanager
from typing import Iterator, Type


class ProcessLockError(RuntimeError):
    """A bounded cross-process lock could not be acquired or released."""


_LOCK_TIMEOUT_SECONDS = 30


@contextmanager
def cross_process_lock(
    account: str,
    lock_directory: str | os.PathLike[str],
    *,
    namespace: str = "Yuki",
    error_type: Type[RuntimeError] = ProcessLockError,
) -> Iterator[None]:
    """Serialize a transaction across processes for one private directory.

    Windows uses a named kernel mutex. POSIX uses flock on a no-follow lock
    file opened relative to a validated private directory descriptor.
    """
    if not isinstance(account, str) or not account or len(account) > 128:
        raise error_type("Invalid cross-process lock identity.")
    if not isinstance(namespace, str) or not namespace.isalnum() or len(namespace) > 32:
        raise error_type("Invalid cross-process lock namespace.")

    if os.name == "nt":
        name = "Local\\Yuki" + namespace + "Lock-" + account
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint)
        kernel32.WaitForSingleObject.restype = ctypes.c_uint
        kernel32.ReleaseMutex.argtypes = (ctypes.c_void_p,)
        kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
        handle = kernel32.CreateMutexW(None, 0, name)
        if not handle:
            raise error_type("Could not create the cross-process lock.")
        try:
            result = kernel32.WaitForSingleObject(handle, _LOCK_TIMEOUT_SECONDS * 1000)
            if result not in (0x00000000, 0x00000080):
                raise error_type("Timed out waiting for the cross-process lock.")
            try:
                yield
            finally:
                if not kernel32.ReleaseMutex(handle):
                    raise error_type("Could not release the cross-process lock.")
        finally:
            kernel32.CloseHandle(handle)
        return

    import fcntl

    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        directory_fd = os.open(lock_directory, directory_flags)
    except OSError as exc:
        raise error_type("The private lock directory is unavailable.") from exc
    try:
        directory_stat = os.fstat(directory_fd)
        if not stat.S_ISDIR(directory_stat.st_mode):
            raise error_type("The lock path is not a directory.")
        if hasattr(os, "getuid") and directory_stat.st_uid != os.getuid():
            raise error_type("The lock directory is not owned by this user.")
        if directory_stat.st_mode & 0o077:
            raise error_type("The lock directory must be private (mode 0700).")
        lock_name = ".yuki-" + namespace.lower() + "-" + account + ".lock"
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        try:
            lock_fd = os.open(lock_name, flags, 0o600, dir_fd=directory_fd)
        except OSError as exc:
            raise error_type("Could not open the cross-process lock file.") from exc
        try:
            lock_stat = os.fstat(lock_fd)
            if not stat.S_ISREG(lock_stat.st_mode):
                raise error_type("The lock file is not a regular file.")
            if hasattr(os, "getuid") and lock_stat.st_uid != os.getuid():
                raise error_type("The lock file is not owned by this user.")
            if lock_stat.st_mode & 0o077:
                os.fchmod(lock_fd, 0o600)
            deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
            while True:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise error_type("Timed out waiting for the cross-process lock.")
                    time.sleep(0.05)
            try:
                yield
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)
    finally:
        os.close(directory_fd)
