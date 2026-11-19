"""Read-only validation of published SQLite backup sets and their manifests."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import stat
from urllib.parse import quote

from core.backup_inventory import SQLiteDatabaseEntry


_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SET_ID = re.compile(r"^[0-9a-f]{32}$")
_MANIFEST_KEYS = {"format_version", "set_id", "created_at", "snapshot_consistency", "databases"}
_DATABASE_KEYS = {"name", "filename", "size_bytes", "sha256", "integrity_check"}


class SQLiteBackupVerificationError(RuntimeError):
    """A backup set is incomplete, malformed, or fails integrity verification."""


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SQLiteBackupVerificationError("manifest_duplicate_json_key")
        result[key] = value
    return result


def _regular_private_file(path: Path) -> None:
    if path.is_symlink():
        raise SQLiteBackupVerificationError("backup_file_symlink_not_allowed")
    try:
        info = path.stat()
    except OSError as exc:
        raise SQLiteBackupVerificationError("backup_file_missing") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise SQLiteBackupVerificationError("backup_file_must_be_single_link_regular_file")


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise SQLiteBackupVerificationError("backup_file_unreadable") from exc
    return digest.hexdigest()


def _check_sqlite(path: Path) -> None:
    uri = "file:" + quote(str(path.resolve(strict=True))) + "?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=10)
        try:
            result = connection.execute("PRAGMA integrity_check").fetchone()
        finally:
            connection.close()
    except (OSError, sqlite3.Error) as exc:
        raise SQLiteBackupVerificationError("sqlite_integrity_check_failed") from exc
    if result is None or result[0] != "ok":
        raise SQLiteBackupVerificationError("sqlite_integrity_check_failed")


def verify_sqlite_backup_set(
    backup_set: str | Path,
    expected_entries: tuple[SQLiteDatabaseEntry, ...],
) -> dict:
    """Verify a backup set without modifying it; return its validated manifest.

    expected_entries is the authoritative inventory. Manifest paths are never
    used to navigate outside the backup-set directory.
    """
    if not isinstance(expected_entries, tuple) or not expected_entries:
        raise SQLiteBackupVerificationError("nonempty_expected_inventory_required")
    expected = {}
    for entry in expected_entries:
        if not isinstance(entry, SQLiteDatabaseEntry):
            raise SQLiteBackupVerificationError("invalid_expected_inventory_entry")
        if not isinstance(entry.name, str) or not _NAME.fullmatch(entry.name):
            raise SQLiteBackupVerificationError("invalid_expected_database_name")
        if entry.name in expected:
            raise SQLiteBackupVerificationError("duplicate_expected_database_name")
        expected[entry.name] = entry

    set_path = Path(backup_set).expanduser()
    if set_path.is_symlink():
        raise SQLiteBackupVerificationError("backup_set_symlink_not_allowed")
    try:
        resolved_set = set_path.resolve(strict=True)
        set_info = resolved_set.stat()
    except OSError as exc:
        raise SQLiteBackupVerificationError("backup_set_missing") from exc
    if not stat.S_ISDIR(set_info.st_mode) or not _SET_ID.fullmatch(resolved_set.name):
        raise SQLiteBackupVerificationError("invalid_backup_set_directory")

    manifest_path = resolved_set / "manifest.json"
    _regular_private_file(manifest_path)
    if manifest_path.stat().st_size > 1024 * 1024:
        raise SQLiteBackupVerificationError("manifest_too_large")
    try:
        with manifest_path.open("r", encoding="utf-8") as source:
            manifest = json.load(source, object_pairs_hook=_unique_object)
    except SQLiteBackupVerificationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SQLiteBackupVerificationError("manifest_invalid_json") from exc

    if not isinstance(manifest, dict) or set(manifest) != _MANIFEST_KEYS:
        raise SQLiteBackupVerificationError("manifest_schema_invalid")
    if type(manifest["format_version"]) is not int or manifest["format_version"] != 1:
        raise SQLiteBackupVerificationError("manifest_version_unsupported")
    if manifest["set_id"] != resolved_set.name or not isinstance(manifest["set_id"], str):
        raise SQLiteBackupVerificationError("manifest_set_id_mismatch")
    if manifest["snapshot_consistency"] != "per_database":
        raise SQLiteBackupVerificationError("manifest_consistency_invalid")
    created_at = manifest["created_at"]
    if not isinstance(created_at, str):
        raise SQLiteBackupVerificationError("manifest_timestamp_invalid")
    try:
        timestamp = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SQLiteBackupVerificationError("manifest_timestamp_invalid") from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise SQLiteBackupVerificationError("manifest_timestamp_must_be_timezone_aware")

    records = manifest["databases"]
    if not isinstance(records, list) or len(records) != len(expected):
        raise SQLiteBackupVerificationError("manifest_database_inventory_mismatch")
    seen = set()
    expected_files = {"manifest.json"}
    for record in records:
        if not isinstance(record, dict) or set(record) != _DATABASE_KEYS:
            raise SQLiteBackupVerificationError("manifest_database_record_invalid")
        name = record["name"]
        if not isinstance(name, str) or name not in expected or name in seen:
            raise SQLiteBackupVerificationError("manifest_database_inventory_mismatch")
        seen.add(name)
        filename = record["filename"]
        expected_filename = name + ".sqlite3"
        if filename != expected_filename:
            raise SQLiteBackupVerificationError("manifest_filename_invalid")
        expected_files.add(expected_filename)
        size = record["size_bytes"]
        if type(size) is not int or size <= 0:
            raise SQLiteBackupVerificationError("manifest_size_invalid")
        if not isinstance(record["sha256"], str) or not _SHA256.fullmatch(record["sha256"]):
            raise SQLiteBackupVerificationError("manifest_digest_invalid")
        if record["integrity_check"] != "ok":
            raise SQLiteBackupVerificationError("manifest_integrity_status_invalid")

        snapshot = resolved_set / expected_filename
        _regular_private_file(snapshot)
        if snapshot.stat().st_size != size:
            raise SQLiteBackupVerificationError("backup_size_mismatch")
        if _digest(snapshot) != record["sha256"]:
            raise SQLiteBackupVerificationError("backup_digest_mismatch")
        _check_sqlite(snapshot)

    try:
        actual_files = {child.name for child in resolved_set.iterdir()}
    except OSError as exc:
        raise SQLiteBackupVerificationError("backup_set_unreadable") from exc
    if actual_files != expected_files:
        raise SQLiteBackupVerificationError("backup_set_file_inventory_mismatch")
    return manifest
