"""Read-only preflight for consolidating Yuki auth and workflow SQLite stores.

This planner never creates, copies, modifies, or deletes files. Its output is
advisory metadata, not authorization to execute a migration.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import os
import sqlite3
import hashlib
import stat
from typing import Literal


StoreStatus = Literal["ready_for_review", "missing", "blocked_unsafe_path", "blocked_unsafe_file", "blocked_corrupt", "blocked_schema"]


_AUTH_TABLES = {
    "auth_users": {
        "user_id", "username", "password_salt", "password_hash",
        "password_iterations", "role", "failed_logins", "locked_until",
        "created_at", "disabled",
    },
    "auth_sessions": {
        "token_hash", "user_id", "created_at", "expires_at", "revoked_at",
    },
}
_WORKFLOW_TABLES = {
    "workflow_runs": {
        "run_id", "idempotency_key", "request_digest", "workflow_name",
        "definition_json", "state", "result_json", "error_code", "created_at",
        "updated_at", "version", "attempts", "lease_owner", "lease_expires_at",
        "approved_by", "approved_at", "approval_digest",
        "approved_session_hash", "approved_expires_at",
    },
    "revoked_approval_sessions": {"session_hash", "revoked_at"},
    "task_events": {
        "event_id", "task_id", "sequence", "kind", "actor_id",
        "payload_json", "created_at",
    },
}


@dataclass(frozen=True)
class FileMetadata:
    exists: bool
    kind: str
    size_bytes: int | None
    mode: int | None
    link_count: int | None


@dataclass(frozen=True)
class SQLiteInventory:
    path: str
    status: StoreStatus
    metadata: FileMetadata
    user_version: int | None = None
    journal_mode: str | None = None
    integrity: str | None = None
    tables: tuple[tuple[str, int], ...] = ()
    missing_tables: tuple[str, ...] = ()
    missing_columns: tuple[tuple[str, tuple[str, ...]], ...] = ()
    schema_objects: tuple[tuple[str, str], ...] = ()
    foreign_key_violations: int | None = None
    nonterminal_runs: int | None = None
    active_leases: int | None = None
    malformed_running_leases: int | None = None
    approvals_without_expiry: int | None = None
    schema_fingerprint_sha256: str | None = None
    sidecars: tuple[tuple[str, FileMetadata], ...] = ()
    reason: str | None = None


@dataclass(frozen=True)
class ControlPlaneMigrationPlan:
    dry_run: bool
    executable: bool
    auth: SQLiteInventory
    workflow: SQLiteInventory
    destination: str
    destination_status: str
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
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
        True, kind, info.st_size if kind == "regular_file" else None,
        stat.S_IMODE(info.st_mode), info.st_nlink,
    )


def _safe_parent_chain(path: Path) -> tuple[bool, str | None]:
    """Check lexical ancestors with lstat; never follow a symlink."""
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    for component in absolute.parts[1:-1]:
        current = current / component
        meta = _metadata(current)
        if not meta.exists:
            return False, "parent_directory_missing"
        if meta.kind == "symlink":
            return False, "parent_component_is_symlink"
        if meta.kind != "directory":
            return False, "parent_component_is_not_directory"
    return True, None


def _inspect_store(path_value: str | Path, expected: dict[str, set[str]]) -> SQLiteInventory:
    path = Path(path_value).expanduser()
    display_path = os.path.abspath(path)
    safe, reason = _safe_parent_chain(path)
    if not safe:
        return SQLiteInventory(
            display_path, "blocked_unsafe_path",
            FileMetadata(False, "uninspected", None, None, None),
            reason=reason,
        )
    metadata = _metadata(path)
    sidecars = tuple(
        (suffix, _metadata(Path(str(path) + suffix)))
        for suffix in ("-wal", "-shm", "-journal")
    )
    if not metadata.exists:
        return SQLiteInventory(display_path, "missing", metadata, sidecars=sidecars, reason="database_missing")
    if metadata.kind != "regular_file" or metadata.link_count != 1:
        return SQLiteInventory(display_path, "blocked_unsafe_file", metadata, sidecars=sidecars, reason="database_must_be_regular_single_link_file")
    try:
        uri = path.resolve(strict=True).as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=5.0) as db:
            db.execute("PRAGMA query_only=ON")
            check = db.execute("PRAGMA quick_check").fetchone()
            integrity = str(check[0]) if check else "no_result"
            if integrity != "ok":
                return SQLiteInventory(display_path, "blocked_corrupt", metadata, integrity=integrity, sidecars=sidecars, reason="sqlite_quick_check_failed")
            user_version = int(db.execute("PRAGMA user_version").fetchone()[0])
            journal_mode = str(db.execute("PRAGMA journal_mode").fetchone()[0]).lower()
            table_rows = db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
            table_names = tuple(str(row[0]) for row in table_rows)
            counts = []
            for table in table_names:
                quoted = '"' + table.replace('"', '""') + '"'
                count = int(db.execute("SELECT COUNT(*) FROM " + quoted).fetchone()[0])
                counts.append((table, count))
            object_rows = db.execute(
                "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
            ).fetchall()
            schema_objects = tuple((str(row[0]), str(row[1])) for row in object_rows)
            schema_material = "\n".join(
                f"{row[0]}:{row[1]}:{row[2] or ''}" for row in object_rows
            ).encode("utf-8")
            schema_fingerprint = hashlib.sha256(schema_material).hexdigest()
            missing_tables = tuple(sorted(set(expected) - set(table_names)))
            missing_columns = []
            for table, required in expected.items():
                if table not in table_names:
                    continue
                quoted = '"' + table.replace('"', '""') + '"'
                columns = {str(row[1]) for row in db.execute("PRAGMA table_info(" + quoted + ")")}
                absent = tuple(sorted(required - columns))
                if absent:
                    missing_columns.append((table, absent))
            fk_violations = len(db.execute("PRAGMA foreign_key_check").fetchall())
            nonterminal_runs = None
            active_leases = None
            malformed_running_leases = None
            approvals_without_expiry = None
            if "workflow_runs" in table_names:
                nonterminal_runs = int(db.execute(
                    "SELECT COUNT(*) FROM workflow_runs WHERE state NOT IN ('succeeded','failed','cancelled','interrupted')"
                ).fetchone()[0])
                active_leases = int(db.execute(
                    "SELECT COUNT(*) FROM workflow_runs WHERE state='running' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL"
                ).fetchone()[0])
                malformed_running_leases = int(db.execute(
                    "SELECT COUNT(*) FROM workflow_runs WHERE state='running' AND (lease_owner IS NULL OR lease_expires_at IS NULL)"
                ).fetchone()[0])
                approvals_without_expiry = int(db.execute(
                    "SELECT COUNT(*) FROM workflow_runs WHERE approved_session_hash IS NOT NULL AND (approved_expires_at IS NULL OR approved_expires_at='')"
                ).fetchone()[0])
            if missing_tables or missing_columns:
                return SQLiteInventory(
                    path=display_path, status="blocked_schema", metadata=metadata,
                    user_version=user_version, journal_mode=journal_mode,
                    integrity=integrity, tables=tuple(counts),
                    missing_tables=missing_tables, missing_columns=tuple(missing_columns),
                    schema_objects=schema_objects, foreign_key_violations=fk_violations,
                    nonterminal_runs=nonterminal_runs, active_leases=active_leases,
                    malformed_running_leases=malformed_running_leases,
                    approvals_without_expiry=approvals_without_expiry,
                    schema_fingerprint_sha256=schema_fingerprint,
                    sidecars=sidecars, reason="required_schema_incomplete",
                )
            if fk_violations:
                return SQLiteInventory(
                    path=display_path, status="blocked_corrupt", metadata=metadata,
                    user_version=user_version, journal_mode=journal_mode,
                    integrity=integrity, tables=tuple(counts),
                    schema_objects=schema_objects, foreign_key_violations=fk_violations,
                    nonterminal_runs=nonterminal_runs, active_leases=active_leases,
                    malformed_running_leases=malformed_running_leases,
                    approvals_without_expiry=approvals_without_expiry,
                    schema_fingerprint_sha256=schema_fingerprint,
                    sidecars=sidecars, reason="foreign_key_check_failed",
                )
            return SQLiteInventory(
                path=display_path, status="ready_for_review", metadata=metadata,
                user_version=user_version, journal_mode=journal_mode,
                integrity=integrity, tables=tuple(counts),
                schema_objects=schema_objects, foreign_key_violations=0,
                nonterminal_runs=nonterminal_runs, active_leases=active_leases,
                malformed_running_leases=malformed_running_leases,
                approvals_without_expiry=approvals_without_expiry,
                schema_fingerprint_sha256=schema_fingerprint,
                sidecars=sidecars,
                reason="metadata_only_preflight_requires_quiesced_sqlite_backup",
            )
    except (OSError, sqlite3.DatabaseError, ValueError, OverflowError):
        return SQLiteInventory(
            display_path, "blocked_corrupt", metadata, sidecars=sidecars,
            reason="sqlite_unreadable_or_invalid",
        )


def plan_control_plane_migration(
    auth_database: str | Path,
    workflow_database: str | Path,
    destination_database: str | Path,
) -> ControlPlaneMigrationPlan:
    """Inventory two source stores and an unused destination without writing."""
    auth_path = Path(os.path.abspath(Path(auth_database).expanduser()))
    workflow_path = Path(os.path.abspath(Path(workflow_database).expanduser()))
    destination_path = Path(os.path.abspath(Path(destination_database).expanduser()))
    if len({auth_path, workflow_path, destination_path}) != 3:
        raise ValueError("auth, workflow, and destination paths must be distinct.")

    auth = _inspect_store(auth_path, _AUTH_TABLES)
    workflow = _inspect_store(workflow_path, _WORKFLOW_TABLES)
    parent_safe, parent_reason = _safe_parent_chain(destination_path)
    if not parent_safe:
        destination_status = "blocked_unsafe_parent:" + str(parent_reason)
    elif _metadata(destination_path).exists:
        destination_status = "blocked_destination_exists"
    else:
        destination_status = "available"

    warnings = [
        "dry_run_only_no_files_created_copied_modified_or_deleted",
        "stop_all_writers_and_workers_before_any_future_backup_or_cutover",
        "use_sqlite_backup_api_for_each_source_database_not_raw_file_copy",
        "preserve_source_databases_and_validate_destination_before_cutover",
        "legacy_approvals_without_expiry_or_valid_session_binding_must_fail_closed",
    ]
    if any(meta.exists for _suffix, meta in auth.sidecars + workflow.sidecars):
        warnings.append("sqlite_sidecars_present_quiesce_writers_and_use_sqlite_backup_api")
    if workflow.nonterminal_runs:
        warnings.append("workflow_contains_nonterminal_runs_reconcile_before_cutover")
    if workflow.malformed_running_leases:
        warnings.append("malformed_running_lease_records_require_manual_reconciliation")
    if workflow.approvals_without_expiry:
        warnings.append("legacy_approval_records_without_expiry_must_be_invalidated")
    executable = (
        auth.status == "ready_for_review"
        and workflow.status == "ready_for_review"
        and destination_status == "available"
    )
    # Even a clean inventory is not migration authorization.
    return ControlPlaneMigrationPlan(
        dry_run=True,
        executable=False,
        auth=auth,
        workflow=workflow,
        destination=str(destination_path),
        destination_status=destination_status,
        warnings=tuple(warnings + (["preflight_conditions_satisfied_but_owner_approval_and_executor_required"] if executable else ["resolve_all_blocking_preflight_findings_before_migration"])),
    )
