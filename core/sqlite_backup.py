"""Consistent, atomic backups for SQLite databases, including WAL databases.

The SQLite online backup API captures a transactionally consistent snapshot;
raw-copying a live database file is intentionally not supported here.
"""
from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import stat
import tempfile
from urllib.parse import quote


class SQLiteBackupError(RuntimeError):
    """A SQLite backup could not be created or verified."""


def backup_sqlite_database(source: str | Path, destination: str | Path) -> Path:
    """Create and verify a private, atomically published SQLite snapshot.

    The destination directory must already exist. The source is opened read-only
    and copied through SQLite's backup API, which includes committed WAL data.
    A failed backup never replaces an existing destination.
    """
    source_path = Path(source).expanduser()
    destination_path = Path(destination).expanduser()
    temporary_path: Path | None = None
    try:
        if source_path.is_symlink() or not source_path.is_file():
            raise SQLiteBackupError("source_must_be_regular_file")
        source_info = source_path.stat()
        if source_info.st_nlink != 1:
            raise SQLiteBackupError("source_hard_links_not_allowed")
        parent = destination_path.parent
        if not parent.is_dir() or parent.is_symlink():
            raise SQLiteBackupError("destination_parent_must_exist")
        if source_path.resolve() == destination_path.resolve(strict=False):
            raise SQLiteBackupError("source_and_destination_must_differ")
        if destination_path.is_symlink():
            raise SQLiteBackupError("destination_symlink_not_allowed")
        if destination_path.exists():
            info = destination_path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise SQLiteBackupError("destination_must_be_private_regular_file")

        fd, temp_name = tempfile.mkstemp(
            prefix=f".{destination_path.name}.", suffix=".tmp", dir=str(parent)
        )
        temporary_path = Path(temp_name)
        os.fchmod(fd, 0o600)
        os.close(fd)

        source_uri = "file:" + quote(str(source_path.resolve())) + "?mode=ro"
        with sqlite3.connect(source_uri, uri=True, timeout=10) as source_db:
            with sqlite3.connect(str(temporary_path), timeout=10) as target_db:
                source_db.backup(target_db, pages=256, sleep=0.05)
                check = target_db.execute("PRAGMA integrity_check").fetchone()
                if check is None or check[0] != "ok":
                    raise SQLiteBackupError("backup_integrity_check_failed")
                target_db.commit()

        os.chmod(temporary_path, 0o600)
        with temporary_path.open("rb") as snapshot:
            os.fsync(snapshot.fileno())
        os.replace(temporary_path, destination_path)
        temporary_path = None
        directory_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return destination_path
    except SQLiteBackupError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise SQLiteBackupError("backup_failed") from exc
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
