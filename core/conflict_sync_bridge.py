"""Opt-in bridge for publishing durable memory conflicts to the sync journal.

This is a local serialization boundary only: it does not send data over a network.
Memory text is included only when the caller explicitly enables sharing.
"""
from __future__ import annotations

from typing import Any

from core.conflict_ledger import ConflictRecord
from core.sync_journal import SyncEvent, SyncJournal

SCHEMA_VERSION = 1
EVENT_KIND = "memory.conflict.recorded"
_MAX_CANDIDATES = 10
_MAX_TEXT_CHARS = 4000


class ConflictSyncError(ValueError):
    """Raised when a conflict cannot safely be converted to a sync event."""


def conflict_event_payload(
    conflict: ConflictRecord, *, share_memory_text: bool = False
) -> dict[str, Any]:
    """Build a versioned, bounded payload; text sharing is explicit opt-in."""
    if not isinstance(conflict, ConflictRecord):
        raise TypeError("conflict must be a ConflictRecord")
    if conflict.status not in {"open", "resolved"}:
        raise ConflictSyncError("unsupported conflict status")
    if not isinstance(share_memory_text, bool):
        raise ConflictSyncError("share_memory_text must be a boolean")
    if not conflict.conflict_id or not conflict.memory_id:
        raise ConflictSyncError("conflict and memory IDs are required")
    if not 2 <= len(conflict.versions) <= _MAX_CANDIDATES:
        raise ConflictSyncError("conflict must contain 2 to 10 candidate versions")

    candidates: list[dict[str, Any]] = []
    for version in conflict.versions:
        if not isinstance(version, dict):
            raise ConflictSyncError("candidate versions must be objects")
        for field in ("version_id", "origin_id", "sequence"):
            if field not in version:
                raise ConflictSyncError(f"candidate is missing {field}")
        version_id, origin_id, sequence = version["version_id"], version["origin_id"], version["sequence"]
        if not isinstance(version_id, str) or not 1 <= len(version_id) <= 200:
            raise ConflictSyncError("invalid candidate version ID")
        if not isinstance(origin_id, str) or not 1 <= len(origin_id) <= 128:
            raise ConflictSyncError("invalid candidate origin ID")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            raise ConflictSyncError("candidate sequence must be a positive integer")
        candidate: dict[str, Any] = {
            "version_id": version_id,
            "origin_id": origin_id,
            "sequence": sequence,
        }
        if share_memory_text:
            text = version.get("text")
            if not isinstance(text, str) or not 1 <= len(text) <= _MAX_TEXT_CHARS:
                raise ConflictSyncError("shared memory text must be 1 to 4000 characters")
            candidate["text"] = text
            category = version.get("category", "note")
            if not isinstance(category, str) or category not in {"note", "preference", "lesson"}:
                raise ConflictSyncError("invalid candidate category")
            candidate["category"] = category
            parent = version.get("parent_version_id")
            if parent is not None:
                if not isinstance(parent, str) or not 1 <= len(parent) <= 200:
                    raise ConflictSyncError("invalid parent version ID")
                candidate["parent_version_id"] = parent
        candidates.append(candidate)

    payload = {
        "schema_version": SCHEMA_VERSION,
        "conflict_id": conflict.conflict_id,
        "memory_id": conflict.memory_id,
        "status": conflict.status,
        "contains_memory_text": share_memory_text,
        "candidates": candidates,
    }
    if conflict.status == "resolved":
        if not conflict.resolved_version_id:
            raise ConflictSyncError("resolved conflict is missing chosen version")
        payload["resolved_version_id"] = conflict.resolved_version_id
    return payload


def publish_conflict(
    journal: SyncJournal,
    conflict: ConflictRecord,
    *,
    allow_conflict_sync: bool = False,
    share_memory_text: bool = False,
) -> SyncEvent:
    """Append a conflict event locally only after explicit sync-policy opt-in.

    Metadata-only mode is the default; candidate text is never included unless
    share_memory_text=True. Callers remain responsible for authenticated transport.
    """
    if not isinstance(journal, SyncJournal):
        raise TypeError("journal must be a SyncJournal")
    if allow_conflict_sync is not True:
        raise ConflictSyncError("conflict sync is disabled; explicit opt-in is required")
    payload = conflict_event_payload(conflict, share_memory_text=share_memory_text)
    return journal.append(EVENT_KIND, conflict.conflict_id, payload)
