"""Read-only retention planning for verified SQLite backup sets."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
import re
import stat
import uuid

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


def _open_directory_at(parent_fd: int, name: str) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    return os.open(name, flags, dir_fd=parent_fd)


def _same_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev == right.st_dev
        and left.st_ino == right.st_ino
        and stat.S_IFMT(left.st_mode) == stat.S_IFMT(right.st_mode)
    )


def _same_file_snapshot(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        _same_identity(left, right)
        and left.st_mode == right.st_mode
        and left.st_size == right.st_size
        and left.st_mtime_ns == right.st_mtime_ns
        and left.st_ctime_ns == right.st_ctime_ns
        and left.st_nlink == right.st_nlink == 1
    )


def prune_sqlite_backup_sets(
    backup_root: str | Path,
    expected_entries: tuple[SQLiteDatabaseEntry, ...],
    *,
    workspace_root: str | Path,
    keep_last: int,
    approved_set_ids: tuple[str, ...],
    explicit_approval: bool,
    pinned_set_ids: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Delete only explicitly approved, freshly re-verified eligible sets.

    This destructive API is POSIX-only and requires dir_fd/no-follow support.
    The caller must obtain human/operator approval for the exact set IDs and
    pass explicit_approval=True. No scheduling or implicit approval exists.
    """
    if explicit_approval is not True:
        raise SQLiteBackupRetentionError("explicit_approval_required")
    if not isinstance(approved_set_ids, tuple) or not approved_set_ids:
        raise SQLiteBackupRetentionError("nonempty_approved_set_ids_required")
    if any(not isinstance(item, str) or not _SET_ID.fullmatch(item) for item in approved_set_ids):
        raise SQLiteBackupRetentionError("invalid_approved_set_id")
    if len(set(approved_set_ids)) != len(approved_set_ids):
        raise SQLiteBackupRetentionError("duplicate_approved_set_id")
    if os.name != "posix" or os.open not in os.supports_dir_fd or os.rename not in os.supports_dir_fd or os.unlink not in os.supports_dir_fd or os.rmdir not in os.supports_dir_fd:
        raise SQLiteBackupRetentionError("safe_dir_fd_deletion_unsupported")

    initial_plan = plan_sqlite_backup_retention(
        backup_root, expected_entries, workspace_root=workspace_root,
        keep_last=keep_last, pinned_set_ids=pinned_set_ids,
    )
    if not set(approved_set_ids).issubset(initial_plan.eligible_set_ids):
        raise SQLiteBackupRetentionError("approved_set_not_eligible")

    root = Path(backup_root).expanduser().resolve(strict=True)
    root_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        root_fd = os.open(root, root_flags)
    except OSError as exc:
        raise SQLiteBackupRetentionError("backup_root_open_failed") from exc

    removed: list[str] = []
    try:
        root_identity = os.fstat(root_fd)
        named_root_identity = os.stat(root, follow_symlinks=False)
        if not stat.S_ISDIR(root_identity.st_mode) or not _same_identity(root_identity, named_root_identity):
            raise SQLiteBackupRetentionError("backup_root_changed")
        for set_id in approved_set_ids:
            # Recompute eligibility immediately before each destructive action.
            current_plan = plan_sqlite_backup_retention(
                root, expected_entries, workspace_root=workspace_root,
                keep_last=keep_last, pinned_set_ids=pinned_set_ids,
            )
            if set_id not in current_plan.eligible_set_ids:
                raise SQLiteBackupRetentionError("approved_set_no_longer_eligible")

            candidate = root / set_id
            try:
                manifest = verify_sqlite_backup_set(candidate, expected_entries)
                expected_files = {"manifest.json"} | {
                    entry.name + ".sqlite3" for entry in expected_entries
                }
                if {item["filename"] for item in manifest["databases"]} != expected_files - {"manifest.json"}:
                    raise SQLiteBackupRetentionError("backup_inventory_changed")
                candidate_fd = _open_directory_at(root_fd, set_id)
            except SQLiteBackupRetentionError:
                raise
            except Exception as exc:
                raise SQLiteBackupRetentionError("candidate_revalidation_failed") from exc

            quarantine = ".yuki-prune-" + uuid.uuid4().hex
            try:
                opened_identity = os.fstat(candidate_fd)
                named_identity = os.stat(set_id, dir_fd=root_fd, follow_symlinks=False)
                if not _same_identity(opened_identity, named_identity):
                    raise SQLiteBackupRetentionError("candidate_identity_changed")

                file_identities = {}
                for filename in expected_files:
                    info = os.stat(filename, dir_fd=candidate_fd, follow_symlinks=False)
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise SQLiteBackupRetentionError("candidate_file_type_or_link_changed")
                    if info.st_mode & 0o077:
                        raise SQLiteBackupRetentionError("candidate_file_permissions_changed")
                    file_identities[filename] = info

                os.rename(set_id, quarantine, src_dir_fd=root_fd, dst_dir_fd=root_fd)
                quarantined_fd = _open_directory_at(root_fd, quarantine)
                try:
                    if not _same_identity(opened_identity, os.fstat(quarantined_fd)):
                        os.rename(quarantine, set_id, src_dir_fd=root_fd, dst_dir_fd=root_fd)
                        raise SQLiteBackupRetentionError("candidate_replaced_during_quarantine")
                    actual_names = set(os.listdir(quarantined_fd))
                    if actual_names != expected_files:
                        os.rename(quarantine, set_id, src_dir_fd=root_fd, dst_dir_fd=root_fd)
                        raise SQLiteBackupRetentionError("candidate_inventory_changed")
                    for filename, prior in file_identities.items():
                        current_info = os.stat(filename, dir_fd=quarantined_fd, follow_symlinks=False)
                        if not _same_file_snapshot(prior, current_info):
                            os.rename(quarantine, set_id, src_dir_fd=root_fd, dst_dir_fd=root_fd)
                            raise SQLiteBackupRetentionError("candidate_file_changed_during_quarantine")
                    # Only the exact, preflighted regular files are unlinked.
                    for filename, prior in file_identities.items():
                        current_info = os.stat(filename, dir_fd=quarantined_fd, follow_symlinks=False)
                        if not _same_file_snapshot(prior, current_info):
                            raise SQLiteBackupRetentionError("candidate_file_changed_before_unlink")
                        os.unlink(filename, dir_fd=quarantined_fd)
                    os.close(quarantined_fd)
                    quarantined_fd = -1
                    os.rmdir(quarantine, dir_fd=root_fd)
                    removed.append(set_id)
                finally:
                    if quarantined_fd >= 0:
                        os.close(quarantined_fd)
            except SQLiteBackupRetentionError:
                raise
            except Exception as exc:
                raise SQLiteBackupRetentionError("candidate_prune_failed") from exc
            finally:
                os.close(candidate_fd)
        return tuple(removed)
    finally:
        os.close(root_fd)
