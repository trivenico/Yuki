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


def prepare_private_sqlite_path(database_path: str | Path) -> str:
    """Create the parent and secure a regular, single-link SQLite database file.

    The final path component is opened without following symlinks where the OS
    supports O_NOFOLLOW. Existing hard-linked files are rejected because
    changing permissions on one name would also affect another path.
    """
    if not isinstance(database_path, (str, Path)) or not str(database_path).strip():
        raise SQLitePathSecurityError("database_path_required")

    requested = Path(database_path).expanduser()
    if requested.name in {"", ".", ".."}:
        raise SQLitePathSecurityError("database_path_invalid")

    try:
        parent = requested.parent.resolve()
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name == "posix":
            parent_info = parent.stat()
            if not stat.S_ISDIR(parent_info.st_mode):
                raise SQLitePathSecurityError("database_parent_must_be_directory")
            if hasattr(os, "geteuid") and parent_info.st_uid != os.geteuid():
                raise SQLitePathSecurityError("database_parent_not_owned_by_current_user")
            # Do not silently chmod an existing directory: it may contain user
            # data unrelated to Yuki. Refuse directories where another user or
            # group can create, replace, or remove the database entry instead.
            if stat.S_IMODE(parent_info.st_mode) & 0o022:
                raise SQLitePathSecurityError("database_parent_writable_by_others")
        target = parent / requested.name
        flags = os.O_RDWR | os.O_CREAT
        flags |= getattr(os, "O_CLOEXEC", 0)
        if os.name == "posix":
            flags |= getattr(os, "O_NOFOLLOW", 0)
        elif target.is_symlink():
            raise SQLitePathSecurityError("database_symlink_not_allowed")

        descriptor = os.open(target, flags, 0o600)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise SQLitePathSecurityError("database_must_be_regular_file")
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

    return str(target)
