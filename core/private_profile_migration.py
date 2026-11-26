"""Non-destructive inventory for legacy private-profile data migration.

This module only inspects filesystem metadata. It never reads file contents,
creates directories, changes permissions, copies, moves, or deletes data.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import os
import stat
from typing import Literal


ItemStatus = Literal[
    "missing",
    "ready_for_review",
    "blocked_collision",
    "blocked_unsafe_source",
    "blocked_unsafe_destination",
    "requires_consistent_sqlite_backup",
]


@dataclass(frozen=True)
class FileMetadata:
    exists: bool
    kind: str
    size_bytes: int | None
    mode: int | None
    link_count: int | None


@dataclass(frozen=True)
class MigrationItem:
    name: str
    source: str
    destination: str
    status: ItemStatus
    metadata: FileMetadata
    sidecars: tuple[tuple[str, FileMetadata], ...] = ()
    reason: str | None = None


@dataclass(frozen=True)
class MigrationPlan:
    dry_run: bool
    executable: bool
    workspace: str
    private_profile_root: str
    items: tuple[MigrationItem, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable report containing paths and metadata only."""
        return asdict(self)


def _metadata(path: Path) -> FileMetadata:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return FileMetadata(False, "missing", None, None, None)
    except OSError:
        return FileMetadata(True, "unreadable", None, None, None)

    if stat.S_ISLNK(info.st_mode):
        kind = "symlink"
    elif stat.S_ISREG(info.st_mode):
        kind = "regular_file"
    elif stat.S_ISDIR(info.st_mode):
        kind = "directory"
    else:
        kind = "other"
    return FileMetadata(
        True,
        kind,
        info.st_size if kind == "regular_file" else None,
        stat.S_IMODE(info.st_mode),
        info.st_nlink,
    )


def _inspect_item(
    name: str,
    source: Path,
    destination: Path,
    *,
    sqlite: bool = False,
) -> MigrationItem:
    source_meta = _metadata(source)
    destination_meta = _metadata(destination)
    sidecars: tuple[tuple[str, FileMetadata], ...] = ()
    if sqlite:
        sidecars = tuple(
            (suffix, _metadata(Path(str(source) + suffix)))
            for suffix in ("-wal", "-shm")
        )

    if not source_meta.exists:
        status: ItemStatus = "missing"
        reason = None
    elif source_meta.kind != "regular_file" or source_meta.link_count != 1:
        status = "blocked_unsafe_source"
        reason = "source_must_be_a_regular_single_link_file"
    elif destination_meta.exists:
        status = "blocked_collision"
        reason = "destination_already_exists_no_overwrite"
    elif destination.parent.exists() and _metadata(destination.parent).kind != "directory":
        status = "blocked_unsafe_destination"
        reason = "destination_parent_is_not_a_directory"
    elif sqlite:
        status = "requires_consistent_sqlite_backup"
        reason = "use_sqlite_backup_api_under_quiescence_not_raw_file_copy"
    else:
        status = "ready_for_review"
        reason = "metadata_only_preflight_not_execution_authorization"

    return MigrationItem(
        name=name,
        source=str(source),
        destination=str(destination),
        status=status,
        metadata=source_meta,
        sidecars=sidecars,
        reason=reason,
    )


def build_private_profile_migration_plan(
    workspace: str | Path,
    private_profile_root: str | Path,
) -> MigrationPlan:
    """Inventory legacy locations without touching their contents or metadata."""
    if not isinstance(workspace, (str, Path)) or not str(workspace).strip():
        raise ValueError("workspace_required")
    if not isinstance(private_profile_root, (str, Path)) or not str(private_profile_root).strip():
        raise ValueError("private_profile_root_required")

    workspace_path = Path(os.path.abspath(Path(workspace).expanduser()))
    profile_path = Path(os.path.abspath(Path(private_profile_root).expanduser()))
    if (\n        workspace_path == profile_path\n        or workspace_path in profile_path.parents\n        or profile_path in workspace_path.parents\n    ):
        raise ValueError("workspace_and_private_profile_roots_overlap")

    items = (
        _inspect_item(
            "local_owner_identity",
            workspace_path / "local-owner-id",
            profile_path / "local-owner-id",
        ),
        _inspect_item(
            "conversation_database",
            workspace_path / "conversations.sqlite3",
            profile_path / "conversations.sqlite3",
            sqlite=True,
        ),
    )
    warnings = (
        "dry_run_only_no_files_read_or_modified",
        "destination_profile_directory_must_be_validated_before_any_future_write",
        "sqlite_wal_and_shm_state_requires_a_consistent_sqlite_backup",
        "owner_identity_contents_are_never_included_in_the_report",
        "no_migration_executor_or_rollback_operation_is_provided",
    )
    return MigrationPlan(
        dry_run=True,
        executable=False,
        workspace=str(workspace_path),
        private_profile_root=str(profile_path),
        items=items,
        warnings=warnings,
    )
