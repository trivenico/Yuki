"""Atomic, integrity-verified backup sets for explicitly inventoried SQLite files."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import tempfile
import uuid

from core.backup_inventory import SQLiteDatabaseEntry
from core.sqlite_backup import backup_sqlite_database


_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class SQLiteBackupSetError(RuntimeError):
    """A complete SQLite backup set could not be safely published."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _integrity_check(path: Path) -> None:
    uri = "file:" + str(path.resolve()) + "?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=10)
        try:
            result = connection.execute("PRAGMA integrity_check").fetchone()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise SQLiteBackupSetError("snapshot_integrity_check_failed") from exc
    if result is None or result[0] != "ok":
        raise SQLiteBackupSetError("snapshot_integrity_check_failed")


def create_sqlite_backup_set(
    entries: tuple[SQLiteDatabaseEntry, ...],
    backup_root: str | Path,
    *,
    workspace_root: str | Path,
) -> Path:
    """Create a complete set and atomically publish its manifest and snapshots.

    backup_root must be pre-provisioned, private, and outside workspace_root.
    The caller is responsible for quiescing cross-database writes when an
    application-wide point-in-time snapshot is required.
    """
    if not isinstance(entries, tuple) or not entries:
        raise SQLiteBackupSetError("nonempty_inventory_tuple_required")
    names: set[str] = set()
    sources: set[Path] = set()
    for entry in entries:
        if not isinstance(entry, SQLiteDatabaseEntry):
            raise SQLiteBackupSetError("invalid_inventory_entry")
        if not isinstance(entry.name, str) or not _NAME.fullmatch(entry.name):
            raise SQLiteBackupSetError("invalid_database_name")
        if entry.name in names:
            raise SQLiteBackupSetError("duplicate_or_empty_database_name")
        source = Path(entry.path)
        if source.is_symlink() or not source.is_file():
            raise SQLiteBackupSetError("source_must_be_regular_file")
        info = source.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise SQLiteBackupSetError("source_must_be_single_link_regular_file")
        resolved = source.resolve(strict=True)
        if resolved in sources:
            raise SQLiteBackupSetError("duplicate_database_source")
        names.add(entry.name)
        sources.add(resolved)

    workspace_input = Path(workspace_root).expanduser()
    root_input = Path(backup_root).expanduser()
    if workspace_input.is_symlink() or root_input.is_symlink():
        raise SQLiteBackupSetError("workspace_or_backup_root_symlink_not_allowed")
    try:
        workspace = workspace_input.resolve(strict=True)
        root = root_input.resolve(strict=True)
    except OSError as exc:
        raise SQLiteBackupSetError("workspace_and_backup_root_must_exist") from exc
    if not workspace.is_dir() or not root.is_dir():
        raise SQLiteBackupSetError("workspace_and_backup_root_must_be_directories")
    try:
        root.relative_to(workspace)
    except ValueError:
        pass
    else:
        raise SQLiteBackupSetError("backup_root_must_be_outside_workspace")
    root_info = root.stat()
    if not stat.S_ISDIR(root_info.st_mode):
        raise SQLiteBackupSetError("backup_root_must_be_directory")
    if os.name == "posix":
        if root_info.st_uid != os.getuid() or root_info.st_mode & 0o077:
            raise SQLiteBackupSetError("backup_root_must_be_owned_and_private")

    set_id = uuid.uuid4().hex
    staging: Path | None = None
    published = root / set_id
    try:
        staging = Path(tempfile.mkdtemp(prefix=".yuki-backup-", dir=root))
        if os.name == "posix":
            os.chmod(staging, 0o700)
        manifest_entries = []
        for entry in sorted(entries, key=lambda item: item.name):
            filename = entry.name + ".sqlite3"
            destination = staging / filename
            backup_sqlite_database(entry.path, destination)
            _integrity_check(destination)
            manifest_entries.append({
                "name": entry.name,
                "filename": filename,
                "size_bytes": destination.stat().st_size,
                "sha256": _sha256(destination),
                "integrity_check": "ok",
            })

        manifest = {
            "format_version": 1,
            "set_id": set_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "snapshot_consistency": "per_database",
            "databases": manifest_entries,
        }
        manifest_path = staging / "manifest.json"
        with manifest_path.open("x", encoding="utf-8") as output:
            json.dump(manifest, output, sort_keys=True, separators=(",", ":"))
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        if os.name == "posix":
            directory_fd = os.open(staging, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        os.rename(staging, published)
        staging = None
        if os.name == "posix":
            directory_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        return published
    except Exception as exc:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        if isinstance(exc, SQLiteBackupSetError):
            raise
        raise SQLiteBackupSetError("backup_set_creation_failed") from exc
