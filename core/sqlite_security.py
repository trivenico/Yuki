import os
from pathlib import Path


def secure_sqlite_file(database_path):
    """Apply owner-only permissions to a SQLite database and sidecars on POSIX."""
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
            os.chmod(candidate, 0o600)
        except FileNotFoundError:
            continue


def prepare_sqlite_path(database_path):
    """Create the parent directory and restrictive DB file before SQLite opens it."""
    path = Path(database_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        flags = os.O_CREAT | os.O_APPEND | os.O_WRONLY
        fd = os.open(str(path), flags, 0o600)
        os.close(fd)
        os.chmod(path, 0o600)
    return str(path)
