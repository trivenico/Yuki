"""Operational health and atomic online backup for the independent audit anchor."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import sqlite3
import stat
import tempfile

from core.audit_anchor_service import SQLiteAuditAnchorService
from core.sqlite_file_security import prepare_private_sqlite_path


_REQUIRED_TABLES = frozenset({
    "anchor_checkpoint", "anchor_publishers", "anchor_admin_audit",
})


class AuditAnchorOperationsError(RuntimeError):
    """Anchor operational check or backup could not be completed safely."""


@dataclass(frozen=True)
class AuditAnchorBackupResult:
    path: str
    sha256: str
    size_bytes: int
    checkpoint_event_id: int | None


def _integrity_check(connection: sqlite3.Connection) -> None:
    rows = connection.execute("PRAGMA integrity_check").fetchall()
    if rows != [("ok",)]:
        raise AuditAnchorOperationsError("anchor_database_integrity_check_failed")
    tables = frozenset(
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    )
    if not _REQUIRED_TABLES.issubset(tables):
        raise AuditAnchorOperationsError("anchor_database_schema_incomplete")


def audit_anchor_readiness(service: SQLiteAuditAnchorService) -> dict:
    """Return a small non-sensitive readiness document; never expose DB details."""
    if not isinstance(service, SQLiteAuditAnchorService):
        raise TypeError("sqlite_audit_anchor_service_required")
    try:
        info = os.stat(service.database_path, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            return {"ready": False, "reason": "database_file_unsafe"}
        if os.name == "posix" and stat.S_IMODE(info.st_mode) & 0o077:
            return {"ready": False, "reason": "database_permissions_unsafe"}
        with service._connect() as db:
            _integrity_check(db)
            checkpoint = db.execute(
                "SELECT event_id, event_hash, published_at, revision "
                "FROM anchor_checkpoint WHERE singleton = 1"
            ).fetchone()
            if checkpoint is not None and (
                type(checkpoint["event_id"]) is not int
                or checkpoint["event_id"] < 0
                or type(checkpoint["revision"]) is not int
                or checkpoint["revision"] < 1
                or not isinstance(checkpoint["event_hash"], str)
                or len(checkpoint["event_hash"]) != 64
                or any(c not in "0123456789abcdef" for c in checkpoint["event_hash"])
                or not isinstance(checkpoint["published_at"], str)
                or not checkpoint["published_at"]
            ):
                return {"ready": False, "reason": "checkpoint_record_invalid"}
        return {
            "ready": True,
            "reason": "ok",
            "checkpoint_initialized": checkpoint is not None,
        }
    except Exception:
        return {"ready": False, "reason": "database_unavailable_or_invalid"}


def create_audit_anchor_backup(
    service: SQLiteAuditAnchorService, destination_path: str
) -> AuditAnchorBackupResult:
    """Create a consistent SQLite online backup and atomically publish the file.

    The destination is secured as a private regular file. A same-directory
    temporary DB is populated through SQLite's backup API, integrity-checked,
    fsynced, and atomically replaced into place. The live DB is never copied
    with filesystem-level byte copying (which is unsafe for active SQLite DBs).
    """
    if not isinstance(service, SQLiteAuditAnchorService):
        raise TypeError("sqlite_audit_anchor_service_required")
    target = Path(prepare_private_sqlite_path(destination_path))
    source = Path(service.database_path).resolve(strict=True)
    if target.resolve() == source:
        raise ValueError("backup_destination_must_differ_from_source")
    fd, temporary = tempfile.mkstemp(
        prefix=".audit-anchor-backup-", suffix=".sqlite3", dir=str(target.parent)
    )
    os.close(fd)
    try:
        with service._connect() as source_db:
            destination_db = sqlite3.connect(temporary, timeout=service.timeout_seconds)
            try:
                source_db.backup(destination_db)
                _integrity_check(destination_db)
                row = destination_db.execute(
                    "SELECT event_id FROM anchor_checkpoint WHERE singleton = 1"
                ).fetchone()
                checkpoint_event_id = int(row[0]) if row is not None else None
                destination_db.execute("PRAGMA journal_mode = DELETE")
            finally:
                destination_db.close()
        file_fd = os.open(temporary, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
        try:
            os.fsync(file_fd)
        finally:
            os.close(file_fd)
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
        if os.name == "posix":
            directory_fd = os.open(target.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        digest = hashlib.sha256()
        with open(target, "rb") as backup_file:
            for chunk in iter(lambda: backup_file.read(1024 * 1024), b""):
                digest.update(chunk)
        return AuditAnchorBackupResult(
            str(target), digest.hexdigest(), target.stat().st_size, checkpoint_event_id
        )
    except AuditAnchorOperationsError:
        raise
    except Exception as exc:
        raise AuditAnchorOperationsError("anchor_backup_failed") from exc
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
