"""Read-only inventory for relocating the private web host's workspace state.

The planner inspects SQLite schemas/metadata and JSON-file metadata only. It
never reads application rows or provider-selection contents and never creates,
copies, modifies, or deletes files. Its report is not migration authorization.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import os

from core.control_plane_migration import (
    FileMetadata,
    SQLiteInventory,
    _inspect_store,
    _metadata,
)
from core.private_profile_migration import _inspect_directory_chain


@dataclass(frozen=True)
class SQLiteResourceInventory:
    name: str
    inventory: SQLiteInventory
    destination: str
    destination_status: str


@dataclass(frozen=True)
class FileResourceInventory:
    name: str
    source: str
    destination: str
    status: str
    metadata: FileMetadata
    reason: str | None = None


@dataclass(frozen=True)
class WebHostMigrationPlan:
    dry_run: bool
    executable: bool
    workspace: str
    private_profile_root: str
    sqlite_resources: tuple[SQLiteResourceInventory, ...]
    file_resources: tuple[FileResourceInventory, ...]
    consistency_groups: tuple[tuple[str, ...], ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


_AUTH = {
    "auth_users": {"user_id", "username", "password_salt", "password_hash",
                   "password_iterations", "role", "failed_logins", "locked_until",
                   "created_at", "disabled"},
    "auth_sessions": {"token_hash", "user_id", "created_at", "expires_at", "revoked_at"},
}
_INFERENCE = {
    "device_pairing_challenges": {"code_hash", "owner_id", "created_at", "expires_at", "consumed_at"},
    "paired_devices": {"device_id", "owner_id", "name", "token_hash", "created_at",
                       "last_seen_at", "revoked_at"},
    "inference_jobs": {"job_id", "owner_id", "model_id", "device_id", "status",
                       "input_text", "result_text", "error_code", "created_at",
                       "updated_at", "lease_expires_at", "lease_token_hash"},
}
_INCOME = {
    "income_opportunities": {"opportunity_id", "owner_id", "platform_id", "title",
                             "category", "source_url", "description", "evidence_level",
                             "status", "currency", "estimated_net_minor",
                             "startup_cost_minor", "estimated_hours", "fit_score",
                             "demand_score", "competition_score", "created_at", "updated_at"},
}


def _file_resource(name: str, source: Path, destination: Path) -> FileResourceInventory:
    source_chain = _inspect_directory_chain(source.parent)
    destination_chain = _inspect_directory_chain(destination.parent)
    if not source_chain.safe:
        status, reason = 'blocked_unsafe_source', source_chain.reason
        metadata = _metadata(source.parent)
    elif not destination_chain.safe:
        status, reason = 'blocked_unsafe_destination', destination_chain.reason
        metadata = _metadata(source)
    else:
        metadata = _metadata(source) if source_chain.missing_at is None else _metadata(source.parent / '__missing__')
        target = _metadata(destination) if destination_chain.missing_at is None else None
        if metadata.exists and (metadata.kind != 'regular_file' or metadata.link_count != 1):
            status, reason = 'blocked_unsafe_source', 'source_must_be_regular_single_link_file'
        elif target is not None and target.exists:
            status, reason = 'blocked_collision', 'destination_already_exists_no_overwrite'
        elif not metadata.exists:
            status, reason = 'missing', None
        else:
            status, reason = 'ready_for_review', 'metadata_only_json_inventory_contents_not_read'
    return FileResourceInventory(name, os.path.abspath(source), os.path.abspath(destination), status, metadata, reason)


def _sqlite_resource(name: str, filename: str, source_root: Path, target_root: Path, expected: dict[str, set[str]]) -> SQLiteResourceInventory:
    source = source_root / filename
    destination = target_root / filename
    inventory = _inspect_store(source, expected)
    chain = _inspect_directory_chain(destination.parent)
    if not chain.safe:
        destination_status = 'blocked_unsafe_destination'
    elif chain.missing_at is not None:
        destination_status = 'available_after_private_root_validation'
    else:
        destination_status = 'blocked_collision' if _metadata(destination).exists else 'available'
    return SQLiteResourceInventory(name, inventory, str(destination), destination_status)

def build_web_host_migration_plan(
    workspace: str | Path,
    private_profile_root: str | Path,
    *,
    storage_backend: str = "sqlite",
) -> WebHostMigrationPlan:
    """Inventory the default web launcher's durable resources without mutation."""
    if not isinstance(workspace, (str, Path)) or not str(workspace).strip():
        raise ValueError("workspace_required")
    if not isinstance(private_profile_root, (str, Path)) or not str(private_profile_root).strip():
        raise ValueError("private_profile_root_required")
    if storage_backend not in {"sqlite", "postgres"}:
        raise ValueError("unsupported_storage_backend")
    source_root = Path(os.path.abspath(Path(workspace).expanduser()))
    target_root = Path(os.path.abspath(Path(private_profile_root).expanduser()))
    if source_root == target_root or source_root in target_root.parents or target_root in source_root.parents:
        raise ValueError("workspace_and_private_profile_roots_overlap")

    specs = [
        ("auth", "auth.sqlite3", _AUTH),
        ("inference_and_device_pairing", "inference.sqlite3", _INFERENCE),
        ("income_opportunities", "income-opportunities.sqlite3", _INCOME),
    ]
    if storage_backend == "sqlite":
        specs.append(("conversations", "conversations.sqlite3", {}))
    sqlite_resources = tuple(
        _sqlite_resource(name, filename, source_root, target_root, expected)
        for name, filename, expected in specs
    )
    file_resources = (
        _file_resource("provider_selection", source_root / "provider-selection.json",
                       target_root / "provider-selection.json"),
    )
    groups = (
        ("auth",),
        ("inference_and_device_pairing",),
        ("income_opportunities",),
        ("conversations",) if storage_backend == "sqlite" else (),
        ("provider_selection",),
    )
    warnings = (
        "dry_run_only_no_files_created_copied_modified_or_deleted",
        "sqlite_inventory_reads_schema_and_aggregate_counts_not_application_rows",
        "sqlite_wal_shm_journal_sidecars_are_reported_by_each_database_inventory",
        "quiesce_all_web_writers_and_use_sqlite_backup_api_for_each_database",
        "inference_jobs_and_device_pairing_share_one_database_and_must_remain_together",
        "provider_selection_json_contents_are_not_read; validate schema and permissions separately",
        "postgres_conversation_backend_is_external_and_requires_a_separate_database_backup_plan",
        "oauth_vault_and_mcp_policy_stores_are_not_constructed_by_the_default_web_launcher",
        "destination_profile_must_be_created_and_validated_before_any_future_write",
        "no_executor_or_rollback_operation_is_provided",
    )
    return WebHostMigrationPlan(
        True, False, str(source_root), str(target_root), sqlite_resources,
        file_resources, groups, warnings,
    )
