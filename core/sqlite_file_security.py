"""Private-file preparation for SQLite databases containing personal data.

On POSIX systems the database file is created or tightened to owner-only
permissions. This is defense in depth, not encryption and not protection from
other processes running as the same OS user. Windows ACLs remain the
responsibility of the user profile / deployment installer.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat


class SQLitePathSecurityError(ValueError):
    """The requested SQLite path is unsafe or cannot be secured."""


def _open_posix_parent_directory(parent: Path) -> int:
    """Open and validate the parent directory, returning a pinned descriptor."""
    required = ("O_DIRECTORY", "O_NOFOLLOW")
    if not all(hasattr(os, flag) for flag in required) or os.open not in os.supports_dir_fd:
        raise SQLitePathSecurityError("secure_directory_relative_open_unavailable")

    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    flags |= getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(parent, flags)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISDIR(info.st_mode):
            raise SQLitePathSecurityError("database_parent_must_be_directory")
        if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
            raise SQLitePathSecurityError("database_parent_not_owned_by_current_user")
        if stat.S_IMODE(info.st_mode) & 0o022:
            raise SQLitePathSecurityError("database_parent_writable_by_others")
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def prepare_private_sqlite_path(database_path: str | Path) -> str:
    """Create the parent and secure a regular, single-link SQLite database.

    POSIX opens the parent once and opens the database basename relative to
    that pinned descriptor. The ownership/mode check and database creation
    therefore address the same directory despite pathname replacement.
    """
    if not isinstance(database_path, (str, Path)) or not str(database_path).strip():
        raise SQLitePathSecurityError("database_path_required")

    requested = Path(database_path).expanduser()
    if requested.name in {"", ".", ".."}:
        raise SQLitePathSecurityError("database_path_invalid")

    parent_descriptor = None
    try:
        parent = requested.parent.resolve()
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = parent / requested.name
        flags = os.O_RDWR | os.O_CREAT
        flags |= getattr(os, "O_CLOEXEC", 0)
        if os.name == "posix":
            parent_descriptor = _open_posix_parent_directory(parent)
            flags |= os.O_NOFOLLOW
            descriptor = os.open(requested.name, flags, 0o600, dir_fd=parent_descriptor)
        else:
            if target.is_symlink():
                raise SQLitePathSecurityError("database_symlink_not_allowed")
            descriptor = os.open(target, flags, 0o600)

        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise SQLitePathSecurityError("database_must_be_regular_file")
            if os.name == "posix" and hasattr(os, "geteuid") and info.st_uid != os.geteuid():
                raise SQLitePathSecurityError("database_not_owned_by_current_user")
            if info.st_nlink != 1:
                raise SQLitePathSecurityError("database_hard_links_not_allowed")
            if os.name == "posix":
                os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
    except SQLitePathSecurityError:
        raise
    except OSError as exc:
        raise SQLitePathSecurityError("database_path_cannot_be_secured") from exc
    finally:
        if parent_descriptor is not None:
            os.close(parent_descriptor)

    return str(target)
