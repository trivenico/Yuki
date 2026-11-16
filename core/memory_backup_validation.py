"""Strict, read-only validation for decrypted memory backup snapshots."""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from core.memory_backup_envelope import decrypt_backup

_MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
_MAX_MEMORIES = 2000
_MEMORY_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_CATEGORIES = frozenset({"note", "preference", "lesson"})
_SNAPSHOT_FIELDS = {
    "format", "schema_version", "owner_id", "exported_at", "memory_count", "memories",
}
_MEMORY_FIELDS = {
    "memory_id", "owner_id", "category", "content", "created_at", "expires_at",
}


class MemoryBackupValidationError(ValueError):
    """Stable validation error that never includes backup contents."""


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise MemoryBackupValidationError("invalid_memory_backup_snapshot")
        result[key] = value
    return result


def _timestamp(value: Any, *, nullable: bool = False) -> datetime | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > 64:
        raise MemoryBackupValidationError("invalid_memory_backup_snapshot")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise MemoryBackupValidationError("invalid_memory_backup_snapshot") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MemoryBackupValidationError("invalid_memory_backup_snapshot")
    return parsed


def validate_memory_backup_snapshot(plaintext: bytes, authenticated_owner_id: str) -> dict:
    """Validate a decrypted snapshot and bind it to the authenticated owner."""
    if not isinstance(plaintext, bytes) or not 1 <= len(plaintext) <= _MAX_SNAPSHOT_BYTES:
        raise MemoryBackupValidationError("invalid_memory_backup_snapshot")
    if not isinstance(authenticated_owner_id, str) or not authenticated_owner_id.strip():
        raise MemoryBackupValidationError("invalid_memory_backup_snapshot")
    try:
        snapshot = json.loads(
            plaintext.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                MemoryBackupValidationError("invalid_memory_backup_snapshot")
            ),
        )
    except MemoryBackupValidationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise MemoryBackupValidationError("invalid_memory_backup_snapshot") from None

    if not isinstance(snapshot, dict) or set(snapshot) != _SNAPSHOT_FIELDS:
        raise MemoryBackupValidationError("invalid_memory_backup_snapshot")
    if snapshot.get("format") != "yuki-memory-snapshot":
        raise MemoryBackupValidationError("unsupported_memory_backup_snapshot")
    if type(snapshot.get("schema_version")) is not int or snapshot["schema_version"] != 1:
        raise MemoryBackupValidationError("unsupported_memory_backup_snapshot")
    if snapshot.get("owner_id") != authenticated_owner_id:
        raise MemoryBackupValidationError("memory_backup_owner_mismatch")
    _timestamp(snapshot.get("exported_at"))

    memories = snapshot.get("memories")
    if (not isinstance(memories, list) or len(memories) > _MAX_MEMORIES
            or type(snapshot.get("memory_count")) is not int
            or snapshot["memory_count"] != len(memories)):
        raise MemoryBackupValidationError("invalid_memory_backup_snapshot")

    seen = set()
    category_counts = {category: 0 for category in sorted(_CATEGORIES)}
    content_bytes = 0
    for item in memories:
        if not isinstance(item, dict) or set(item) != _MEMORY_FIELDS:
            raise MemoryBackupValidationError("invalid_memory_backup_snapshot")
        memory_id = item.get("memory_id")
        if not isinstance(memory_id, str) or not _MEMORY_ID.fullmatch(memory_id):
            raise MemoryBackupValidationError("invalid_memory_backup_snapshot")
        if memory_id in seen:
            raise MemoryBackupValidationError("duplicate_memory_backup_id")
        seen.add(memory_id)
        if item.get("owner_id") != authenticated_owner_id:
            raise MemoryBackupValidationError("memory_backup_owner_mismatch")
        category = item.get("category")
        if not isinstance(category, str) or category not in _CATEGORIES:
            raise MemoryBackupValidationError("invalid_memory_backup_snapshot")
        category_counts[category] += 1
        content = item.get("content")
        if not isinstance(content, str) or not content.strip() or len(content) > 2000:
            raise MemoryBackupValidationError("invalid_memory_backup_snapshot")
        content_bytes += len(content.encode("utf-8"))
        if content_bytes > _MAX_SNAPSHOT_BYTES:
            raise MemoryBackupValidationError("invalid_memory_backup_snapshot")
        _timestamp(item.get("created_at"))
        _timestamp(item.get("expires_at"), nullable=True)

    return {
        "owner_id": authenticated_owner_id,
        "memory_count": len(memories),
        "category_counts": category_counts,
        "memory_ids": tuple(item["memory_id"] for item in memories),
    }


def decrypt_and_validate_memory_backup(
    envelope: bytes, password: str, authenticated_owner_id: str,
) -> dict:
    """Authenticate the envelope, then strictly validate owner-bound snapshot."""
    plaintext = decrypt_backup(envelope, password)
    return validate_memory_backup_snapshot(plaintext, authenticated_owner_id)
