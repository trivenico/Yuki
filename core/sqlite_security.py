"""Restrictive permissions for local SQLite approval state.

These helpers reduce exposure to other local OS users. They do not protect against
the same OS account or an administrator, and SQLite files should live in a private
workspace directory.
"""
import os
from pathlib import Path
import stat


def _open_regular_file(path, flags):
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(path), flags | nofollow)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("SQLite storage path must be a regular file.")
        return fd
    except Exception:
        os.close(fd)
        raise


def secure_sqlite_file(database_path):
    """Apply owner-only permissions to SQLite DB and existing sidecars on POSIX."""
    if os.name != "posix":
        return
    path = Path(database_path).expanduser()
    for candidate in (
        path,
        Path(str(path) + "-wal"),
        Path(str(path) + "-shm"),
        Path(str(path) + "-journal"),
    ):
        try:
            fd = _open_regular_file(candidate, os.O_RDONLY)
        except FileNotFoundError:
            continue
        try:
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)


def prepare_sqlite_path(database_path):
    """Create a regular, owner-only DB file without following a symlink on POSIX."""
    path = Path(database_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        flags = os.O_CREAT | os.O_APPEND | os.O_WRONLY
        fd = _open_regular_file(path, flags)
        try:
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
    return str(path)
