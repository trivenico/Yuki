"""Restore verified SQLite snapshots into a new isolated directory."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
from typing import Callable, Mapping
import uuid

from core.backup_inventory import SQLiteDatabaseEntry
from core.sqlite_backup import backup_sqlite_database
from core.sqlite_backup_verify import verify_sqlite_backup_set


class SQLiteRestoreVerificationError(RuntimeError):
    """An isolated restore could not be completed and validated."""


def restore_and_verify_sqlite_backup_set(
    backup_set: str | Path,
    expected_entries: tuple[SQLiteDatabaseEntry, ...],
    restore_root: str | Path,
    *,
    workspace_root: str | Path,
    application_validator: Callable[[Mapping[str, Path]], bool],
) -> Path:
    """Restore a verified set into a fresh directory and validate it before publish.

    The application validator must be trusted application code, must open the
    restored databases read-only, and must return exactly True. It receives
    logical database names mapped to paths inside the private staging directory.
    No live database path is accepted as a destination.
    """
    if not callable(application_validator):
        raise SQLiteRestoreVerificationError("application_validator_required")
    try:
        manifest = verify_sqlite_backup_set(backup_set, expected_entries)
    except Exception as exc:
        raise SQLiteRestoreVerificationError("source_backup_set_not_verified") from exc

    workspace_input = Path(workspace_root).expanduser()
    root_input = Path(restore_root).expanduser()
    if workspace_input.is_symlink() or root_input.is_symlink():
        raise SQLiteRestoreVerificationError("workspace_or_restore_root_symlink_not_allowed")
    try:
        workspace = workspace_input.resolve(strict=True)
        root = root_input.resolve(strict=True)
    except OSError as exc:
        raise SQLiteRestoreVerificationError("workspace_and_restore_root_must_exist") from exc
    if not workspace.is_dir() or not root.is_dir():
        raise SQLiteRestoreVerificationError("workspace_and_restore_root_must_be_directories")
    try:
        root.relative_to(workspace)
    except ValueError:
        pass
    else:
        raise SQLiteRestoreVerificationError("restore_root_must_be_outside_workspace")
    source_set = Path(backup_set).resolve(strict=True)
    try:
        root.relative_to(source_set)
    except ValueError:
        pass
    else:
        raise SQLiteRestoreVerificationError("restore_root_must_be_outside_backup_set")

    root_info = root.stat()
    if os.name == "posix" and (
        root_info.st_uid != os.getuid() or root_info.st_mode & 0o077
    ):
        raise SQLiteRestoreVerificationError("restore_root_must_be_owned_and_private")

    restore_id = "restore-" + uuid.uuid4().hex
    staging: Path | None = None
    published = root / restore_id
    try:
        staging = Path(tempfile.mkdtemp(prefix=".yuki-restore-", dir=root))
        if os.name == "posix":
            os.chmod(staging, 0o700)

        restored: dict[str, Path] = {}
        report_databases = []
        for record in manifest["databases"]:
            name = record["name"]
            source = source_set / (name + ".sqlite3")
            destination = staging / (name + ".sqlite3")
            backup_sqlite_database(source, destination)
            restored[name] = destination
            report_databases.append({
                "name": name,
                "filename": destination.name,
                "size_bytes": destination.stat().st_size,
            })

        try:
            validation_result = application_validator(dict(restored))
        except Exception as exc:
            raise SQLiteRestoreVerificationError("application_validation_failed") from exc
        if validation_result is not True:
            raise SQLiteRestoreVerificationError("application_validation_rejected_restore")

        report = {
            "format_version": 1,
            "restore_id": restore_id,
            "source_backup_set_id": manifest["set_id"],
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "sqlite_integrity": "ok",
            "application_validation": "passed",
            "databases": report_databases,
        }
        report_path = staging / "restore-report.json"
        with report_path.open("x", encoding="utf-8") as output:
            json.dump(report, output, sort_keys=True, separators=(",", ":"))
            output.write("\\n")
            output.flush()
            if os.name == "posix":
                os.fchmod(output.fileno(), 0o600)
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
        if isinstance(exc, SQLiteRestoreVerificationError):
            raise
        raise SQLiteRestoreVerificationError("isolated_restore_failed") from exc
