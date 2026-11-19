"""Read-only retention planning for verified SQLite backup sets."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
import re
import stat

from core.backup_inventory import SQLiteDatabaseEntry
from core.sqlite_backup_verify import verify_sqlite_backup_set


_SET_ID = re.compile(r"^[0-9a-f]{32}$")


class SQLiteBackupRetentionError(RuntimeError):
    """Retention cannot be planned safely from the configured backup root."""


@dataclass(frozen=True)
class SQLiteBackupRetentionPlan:
    """Immutable dry-run result; this module never deletes backup data."""

    verified_set_ids: tuple[str, ...]
    retained_set_ids: tuple[str, ...]
    eligible_set_ids: tuple[str, ...]


def plan_sqlite_backup_retention(
    backup_root: str | Path,
    expected_entries: tuple[SQLiteDatabaseEntry, ...],
    *,
    workspace_root: str | Path,
    keep_last: int,
    pinned_set_ids: tuple[str, ...] = (),
) -> SQLiteBackupRetentionPlan:
    """Plan pruning candidates after verifying every published set.

    The newest `keep_last` sets and every pinned set are retained. All other
    sets are merely reported as eligible; no filesystem mutation is performed.
    Any unexpected root entry or unverifiable set aborts the entire plan.
    """
    if type(keep_last) is not int or keep_last < 1:
        raise SQLiteBackupRetentionError("keep_last_must_be_positive_integer")
    if not isinstance(pinned_set_ids, tuple):
        raise SQLiteBackupRetentionError("pinned_set_ids_must_be_tuple")
    if any(not isinstance(item, str) or not _SET_ID.fullmatch(item) for item in pinned_set_ids):
        raise SQLiteBackupRetentionError("invalid_pinned_set_id")
    if len(set(pinned_set_ids)) != len(pinned_set_ids):
        raise SQLiteBackupRetentionError("duplicate_pinned_set_id")

    root_input = Path(backup_root).expanduser()
    workspace_input = Path(workspace_root).expanduser()
    if root_input.is_symlink() or workspace_input.is_symlink():
        raise SQLiteBackupRetentionError("root_symlink_not_allowed")
    try:
        root = root_input.resolve(strict=True)
        workspace = workspace_input.resolve(strict=True)
    except OSError as exc:
        raise SQLiteBackupRetentionError("roots_must_exist") from exc
    if not root.is_dir() or not workspace.is_dir():
        raise SQLiteBackupRetentionError("roots_must_be_directories")
    try:
        root.relative_to(workspace)
    except ValueError:
        pass
    else:
        raise SQLiteBackupRetentionError("backup_root_must_be_outside_workspace")
    root_info = root.stat()
    if not stat.S_ISDIR(root_info.st_mode):
        raise SQLiteBackupRetentionError("backup_root_must_be_directory")
    if os.name == "posix" and (root_info.st_uid != os.getuid() or root_info.st_mode & 0o077):
        raise SQLiteBackupRetentionError("backup_root_must_be_owned_and_private")

    try:
        children = sorted(root.iterdir(), key=lambda path: path.name)
    except OSError as exc:
        raise SQLiteBackupRetentionError("backup_root_unreadable") from exc

    verified: list[tuple[datetime, str]] = []
    for child in children:
        if child.is_symlink() or not child.is_dir() or not _SET_ID.fullmatch(child.name):
            raise SQLiteBackupRetentionError("unexpected_backup_root_entry")
        try:
            manifest = verify_sqlite_backup_set(child, expected_entries)
            created_at = datetime.fromisoformat(manifest["created_at"].replace("Z", "+00:00"))
        except Exception as exc:
            raise SQLiteBackupRetentionError("backup_set_verification_failed") from exc
        verified.append((created_at, child.name))

    verified.sort(key=lambda item: (item[0], item[1]), reverse=True)
    ordered_ids = tuple(set_id for _, set_id in verified)
    missing_pins = set(pinned_set_ids).difference(ordered_ids)
    if missing_pins:
        raise SQLiteBackupRetentionError("pinned_backup_set_missing")

    retained = set(ordered_ids[:keep_last]) | set(pinned_set_ids)
    retained_ids = tuple(set_id for set_id in ordered_ids if set_id in retained)
    eligible_ids = tuple(set_id for set_id in ordered_ids if set_id not in retained)
    if ordered_ids and not retained_ids:
        raise SQLiteBackupRetentionError("last_known_good_must_be_retained")
    return SQLiteBackupRetentionPlan(
        verified_set_ids=ordered_ids,
        retained_set_ids=retained_ids,
        eligible_set_ids=eligible_ids,
    )
