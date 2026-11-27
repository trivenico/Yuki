"""Crash-aware sidecar receipts for desktop backup restoration.

The receipt helpers are intentionally separate from restore orchestration. Callers
must create PREPARED before publishing a profile, preserve it on ambiguous failures,
and only mark COMMITTED after the destination parent has been fsynced.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import stat
import time
from pathlib import Path

_SCHEMA_VERSION = 1
_STATES = {"PREPARED", "COMMITTED", "NOT_PUBLISHED", "RECOVERY_REQUIRED"}
_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_MAX_RECEIPT_BYTES = 16 * 1024


class RestoreReceiptError(RuntimeError):
    """Receipt is unsafe, malformed, or cannot be durably updated."""


def _fsync_parent(parent: Path) -> None:
    # Do not silently claim durability on platforms where this implementation
    # has not established a directory-flush primitive.
    if os.name != "posix":
        raise RestoreReceiptError("Durable restore receipts are unsupported on this platform.")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(parent, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _receipt_path(parent: Path, operation_id: str) -> Path:
    if not isinstance(operation_id, str) or not _ID_RE.fullmatch(operation_id):
        raise RestoreReceiptError("Restore operation ID is invalid.")
    return parent / (".yuki-restore-" + operation_id + ".json")


def _validate_parent(parent: Path) -> Path:
    try:
        resolved = Path(parent).resolve(strict=True)
        info = resolved.stat()
    except OSError as exc:
        raise RestoreReceiptError("Restore receipt directory is unavailable.") from exc
    if (
        not stat.S_ISDIR(info.st_mode)
        or (hasattr(os, "getuid") and info.st_uid != os.getuid())
        or (os.name == "posix" and info.st_mode & 0o022)
    ):
        raise RestoreReceiptError(
            "Restore receipt directory must be owned by the current user and not group/world writable."
        )
    return resolved


def _write_new(path: Path, payload: dict) -> None:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        if os.name == "posix":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(fd)
    finally:
        os.close(fd)


def create_restore_receipt(destination: Path, *, file_count: int, size_bytes: int) -> dict:
    """Create and durably publish a PREPARED receipt beside destination."""
    target = Path(destination).expanduser()
    if target.name in {"", ".", ".."} or target.name != target.name.strip():
        raise RestoreReceiptError("Restore destination name is invalid.")
    parent = _validate_parent(target.parent)
    if target.is_symlink() or target.exists():
        raise RestoreReceiptError("Restore destination must not exist before receipt creation.")
    if isinstance(file_count, bool) or not isinstance(file_count, int) or file_count < 0:
        raise RestoreReceiptError("Restore file count is invalid.")
    if isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or size_bytes < 0:
        raise RestoreReceiptError("Restore size is invalid.")
    operation_id = secrets.token_hex(16)
    receipt = {
        "schema_version": _SCHEMA_VERSION,
        "operation_id": operation_id,
        "destination_name": target.name,
        "state": "PREPARED",
        "created_at": time.time(),
        "updated_at": time.time(),
        "file_count": file_count,
        "size_bytes": size_bytes,
    }
    path = _receipt_path(parent, operation_id)
    try:
        _write_new(path, receipt)
        _fsync_parent(parent)
    except Exception:
        # Do not remove a possibly-created receipt after a failed directory
        # flush: its presence is useful recovery evidence.
        raise
    return {"operation_id": operation_id, "receipt_path": str(path), **receipt}


def _read_validated(path: Path, expected_parent: Path, operation_id: str) -> dict:
    expected = _receipt_path(expected_parent, operation_id)
    if path != expected:
        raise RestoreReceiptError("Restore receipt path does not match its operation ID and parent.")
    try:
        info = path.lstat()
    except OSError as exc:
        raise RestoreReceiptError("Restore receipt is unavailable.") from exc
    if (
        stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1 or (os.name == "posix" and info.st_mode & 0o077)
        or (hasattr(os, "getuid") and info.st_uid != os.getuid())
        or info.st_size <= 0 or info.st_size > _MAX_RECEIPT_BYTES
    ):
        raise RestoreReceiptError("Restore receipt permissions or file type are unsafe.")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        opened = os.fstat(fd)
        if opened.st_ino != info.st_ino or opened.st_dev != info.st_dev:
            raise RestoreReceiptError("Restore receipt changed while being opened.")
        chunks = []
        remaining = _MAX_RECEIPT_BYTES + 1
        while remaining:
            chunk = os.read(fd, min(4096, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
    finally:
        os.close(fd)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise RestoreReceiptError("Restore receipt is malformed.") from exc
    required = {
        "schema_version", "operation_id", "destination_name", "state",
        "created_at", "updated_at", "file_count", "size_bytes",
    }
    if (
        not isinstance(data, dict) or set(data) != required
        or data["schema_version"] != _SCHEMA_VERSION
        or data["operation_id"] != operation_id
        or not isinstance(data["state"], str) or data["state"] not in _STATES
        or not isinstance(data["destination_name"], str)
        or not data["destination_name"] or Path(data["destination_name"]).name != data["destination_name"]
        or not isinstance(data["file_count"], int) or isinstance(data["file_count"], bool)
        or data["file_count"] < 0
        or not isinstance(data["size_bytes"], int) or isinstance(data["size_bytes"], bool)
        or data["size_bytes"] < 0
        or not isinstance(data["created_at"], (int, float))
        or not isinstance(data["updated_at"], (int, float))
    ):
        raise RestoreReceiptError("Restore receipt schema or values are invalid.")
    return data


def update_restore_receipt(receipt_path: Path, operation_id: str, state: str) -> dict:
    """Atomically update a receipt state and fsync the file and parent directory."""
    if state not in {"COMMITTED", "NOT_PUBLISHED", "RECOVERY_REQUIRED"}:
        raise RestoreReceiptError("Restore receipt state transition is not allowed.")
    path = Path(receipt_path)
    parent = _validate_parent(path.parent)
    current = _read_validated(path, parent, operation_id)
    if current["state"] != "PREPARED":
        raise RestoreReceiptError("Restore receipt is already terminal.")
    current["state"] = state
    current["updated_at"] = time.time()
    temporary = parent / (".yuki-restore-update-" + secrets.token_hex(16) + ".tmp")
    try:
        _write_new(temporary, current)
        os.replace(temporary, path)
        _fsync_parent(parent)
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise
    return current


def inspect_restore_receipt(receipt_path: Path, *, expected_parent: Path, operation_id: str) -> dict:
    """Read-only inspection; PREPARED is never promoted to a success state."""
    parent = _validate_parent(Path(expected_parent))
    path = Path(receipt_path)
    data = _read_validated(path, parent, operation_id)
    destination = parent / data["destination_name"]
    if destination.is_symlink():
        raise RestoreReceiptError("Restore destination is a symbolic link.")
    exists = destination.exists()
    inventory_valid = None
    inventory_error = None
    if exists:
        try:
            if not destination.is_dir():
                raise RestoreReceiptError("Restore destination is not a directory.")
            profile_root = destination / ".yuki"
            if profile_root.is_symlink() or not profile_root.is_dir():
                raise RestoreReceiptError("Restored profile directory is missing or unsafe.")
            from ui.desktop.backup import _inventory, validate_coding_task_backup_state
            _inventory(destination)
            validate_coding_task_backup_state(profile_root)
            inventory_valid = True
        except Exception:
            inventory_valid = False
            inventory_error = "Destination inventory validation failed; preserve it for manual review."
    state = data["state"]
    if state == "PREPARED":
        observed = "PUBLISHED_UNCONFIRMED" if exists else "NOT_CURRENTLY_PUBLISHED"
    elif state == "COMMITTED":
        observed = "COMMITTED" if exists and inventory_valid is True else "RECOVERY_REQUIRED"
    elif state == "NOT_PUBLISHED":
        observed = "NOT_PUBLISHED" if not exists else "RECOVERY_REQUIRED"
    else:
        observed = "RECOVERY_REQUIRED"
    return {
        **data,
        "receipt_path": str(path),
        "destination": str(destination),
        "destination_exists": exists,
        "destination_inventory_valid": inventory_valid,
        "destination_inventory_error": inventory_error,
        "observed_outcome": observed,
    }
