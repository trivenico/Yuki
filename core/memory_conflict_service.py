"""Bridge deterministic memory reconciliation to the persistent conflict ledger.

Only concurrent/ancestry-unknown edits are persisted. Sequential and identical
versions are intentionally no-ops. This adapter never chooses a winner.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.memory_conflicts import MemoryReconciliation, MemoryVersion
from core.conflict_ledger import ConflictLedger, ConflictRecord


def _version_payload(version: MemoryVersion) -> dict[str, Any]:
    version.validate()
    return {
        "memory_id": version.memory_id,
        "version_id": version.version_id,
        "origin_id": version.origin_id,
        "sequence": version.sequence,
        "text": version.text,
        "category": version.category,
        "parent_version_id": version.parent_version_id,
    }


def persist_reconciliation(
    ledger: ConflictLedger,
    reconciliation: MemoryReconciliation,
    *,
    created_at: str | None = None,
) -> ConflictRecord | None:
    """Persist a conflict; return None for identical or explicitly sequential data."""
    if not isinstance(ledger, ConflictLedger):
        raise TypeError("ledger must be a ConflictLedger")
    if not isinstance(reconciliation, MemoryReconciliation):
        raise TypeError("reconciliation must be a MemoryReconciliation")
    if reconciliation.status in {"identical", "incoming_is_child", "local_is_child"}:
        return None
    if reconciliation.status != "conflict":
        raise ValueError("unsupported reconciliation status")
    if not reconciliation.conflict_id or len(reconciliation.versions) < 2:
        raise ValueError("conflict reconciliation must include an ID and at least two versions")
    if any(version.memory_id != reconciliation.memory_id for version in reconciliation.versions):
        raise ValueError("all conflict versions must match the reconciliation memory ID")
    timestamp = created_at or datetime.now(timezone.utc).isoformat()
    return ledger.record_conflict(
        conflict_id=reconciliation.conflict_id,
        memory_id=reconciliation.memory_id,
        versions=[_version_payload(version) for version in reconciliation.versions],
        created_at=timestamp,
    )
