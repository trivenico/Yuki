"""Consistent, validated SQLite backups for Yuki's execution journal.

The SQLite backup API captures a transactionally consistent snapshot even when
the source database uses WAL mode. A validated temporary file is atomically
published only after integrity_check succeeds.
"""
from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import tempfile


class JournalBackupError(RuntimeError):
    """A journal backup could not be safely created or validated."""


def backup_sqlite_database(source: str | Path, destination: str | Path) -> Path:
    """Create a consistent SQLite backup and atomically publish it.

    Existing destinations are replaced only after the temporary backup passes
    SQLite's integrity check. The source must already exist as a regular file.
    """
    source_path = Path(source).expanduser()
    destination_path = Path(destination).expanduser()

    try:
        source_resolved = source_path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise JournalBackupError("backup source does not exist or cannot be resolved") from exc
    if not source_resolved.is_file():
        raise JournalBackupError("backup source must be a regular file")

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    destination_resolved = destination_path.resolve(strict=False)
    if source_resolved == destination_resolved:
        raise JournalBackupError("backup destination must differ from source")

    temporary_path: Path | None = None
    try:
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{destination_path.name}.", suffix=".tmp",
            dir=str(destination_path.parent),
        )
        os.close(fd)
        temporary_path = Path(temporary_name)

        # backup() obtains a consistent SQLite snapshot, including WAL content.
        with sqlite3.connect(str(source_resolved), timeout=5.0) as source_db:
            with sqlite3.connect(str(temporary_path), timeout=5.0) as backup_db:
                source_db.backup(backup_db)
                check = backup_db.execute("PRAGMA integrity_check").fetchone()
                if check is None or check[0] != "ok":
                    raise JournalBackupError("backup failed SQLite integrity validation")

        # Ensure bytes are flushed before publishing the completed backup.
        with temporary_path.open("rb") as backup_file:
            os.fsync(backup_file.fileno())
        os.replace(temporary_path, destination_path)
        temporary_path = None
        return destination_path
    except JournalBackupError:
        raise
    except (OSError, sqlite3.DatabaseError) as exc:
        raise JournalBackupError("could not create a consistent journal backup") from exc
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
