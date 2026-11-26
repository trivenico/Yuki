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
class PathChainInspection:
    safe: bool
    checked: tuple[str, ...]
    missing_at: str | None = None
    unsafe_at: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class MigrationItem:
    name: str
    source: str
    destination: str
    status: ItemStatus
    metadata: FileMetadata
    source_parent_chain: PathChainInspection
    destination_parent_chain: PathChainInspection
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


def _inspect_directory_chain(path: Path) -> PathChainInspection:
    """Inspect each lexical directory component with lstat, never following links."""
    absolute = Path(os.path.abspath(path))
    checked: list[str] = []
    current = Path(absolute.anchor)
    try:
        root_meta = _metadata(current)
        checked.append(str(current))
        if not root_meta.exists or root_meta.kind != "directory":
            return PathChainInspection(
                False, tuple(checked), unsafe_at=str(current),
                reason="path_root_is_not_a_directory",
            )
        for component in absolute.parts[1:]:
            current = current / component
            meta = _metadata(current)
            checked.append(str(current))
            if not meta.exists:
                return PathChainInspection(
                    True, tuple(checked), missing_at=str(current),
                    reason="remaining_path_components_not_present",
                )
            if meta.kind == "symlink":
                return PathChainInspection(
                    False, tuple(checked), unsafe_at=str(current),
                    reason="path_component_is_symlink",
                )
            if meta.kind != "directory":
                return PathChainInspection(
                    False, tuple(checked), unsafe_at=str(current),
                    reason="path_component_is_not_directory",
                )
    except (OSError, ValueError):
        return PathChainInspection(
            False, tuple(checked), unsafe_at=str(current),
            reason="path_chain_inspection_failed",
        )
    return PathChainInspection(True, tuple(checked))


def _inspect_item(
    name: str,
    source: Path,
    destination: Path,
    *,
    sqlite: bool = False,
) -> MigrationItem:
    source_chain = _inspect_directory_chain(source.parent)
    destination_chain = _inspect_directory_chain(destination.parent)
    sidecars: tuple[tuple[str, FileMetadata], ...] = ()

    # Do not lstat a leaf until its parent chain has been proven to contain
    # directories only; lstat(leaf) still follows symlinked parent components.
    source_meta = (
        _metadata(source)
        if source_chain.safe and source_chain.missing_at is None
        else FileMetadata(False, "uninspected", None, None, None)
    )
    destination_meta = (
        _metadata(destination)
        if destination_chain.safe and destination_chain.missing_at is None
        else FileMetadata(False, "uninspected", None, None, None)
    )
    if sqlite and source_chain.safe and source_chain.missing_at is None:
        sidecars = tuple(
            (suffix, _metadata(Path(str(source) + suffix)))
            for suffix in ("-wal", "-shm")
        )

    if not source_chain.safe:
        status: ItemStatus = "blocked_unsafe_source"
        reason = source_chain.reason or "source_parent_chain_is_unsafe"
    elif source_chain.missing_at is not None:
        status = "missing"
        reason = None
    elif not source_meta.exists:
        status = "missing"
        reason = None
    elif source_meta.kind != "regular_file" or source_meta.link_count != 1:
        status = "blocked_unsafe_source"
        reason = "source_must_be_a_regular_single_link_file"
    elif not destination_chain.safe:
        status = "blocked_unsafe_destination"
        reason = destination_chain.reason or "destination_parent_chain_is_unsafe"
    elif destination_chain.missing_at is not None:
        status = "requires_consistent_sqlite_backup" if sqlite else "ready_for_review"
        reason = (
            "use_sqlite_backup_api_under_quiescence_not_raw_file_copy"
            if sqlite else "metadata_only_preflight_not_execution_authorization"
        )
    elif destination_meta.exists:
        status = "blocked_collision"
        reason = "destination_already_exists_no_overwrite"
    elif sqlite:
        status = "requires_consistent_sqlite_backup"
        reason = "use_sqlite_backup_api_under_quiescence_not_raw_file_copy"
    else:
        status = "ready_for_review"
        reason = "metadata_only_preflight_not_execution_authorization"

    if sqlite and sidecars:
        unsafe_sidecars = [
            suffix for suffix, meta in sidecars
            if meta.exists and (
                meta.kind != "regular_file" or meta.link_count != 1
            )
        ]
        if unsafe_sidecars:
            reason = "sqlite_sidecar_requires_separate_safe_handling:" + ",".join(
                unsafe_sidecars
            )

    return MigrationItem(
        name=name,
        source=str(source),
        destination=str(destination),
        status=status,
        metadata=source_meta,
        source_parent_chain=source_chain,
        destination_parent_chain=destination_chain,
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
    if (
        workspace_path == profile_path
        or workspace_path in profile_path.parents
        or profile_path in workspace_path.parents
    ):
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
        "parent_chains_are_lstat_inspected_but_may_change_after_the_report",
        "destination_profile_directory_must_be_validated_before_any_future_write",
        "sqlite_wal_and_shm_state_requires_a_consistent_sqlite_backup",
        "sqlite_sidecars_are_metadata_only_and_require_separate_safe_handling",
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
