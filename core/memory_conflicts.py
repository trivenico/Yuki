"""Deterministic conflict detection for replicated Yuki memory records.

This module does not perform network I/O or choose a winner based on clocks.
Concurrent edits are preserved as an explicit conflict for the caller to resolve.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
import hashlib
import json


class MemoryConflictError(ValueError):
    """Raised when a memory version is malformed or internally inconsistent."""


@dataclass(frozen=True)
class MemoryVersion:
    memory_id: str
    version_id: str
    origin_id: str
    sequence: int
    text: str
    category: str = "note"
    parent_version_id: str | None = None

    def validate(self) -> "MemoryVersion":
        if not isinstance(self.memory_id, str) or not self.memory_id.strip() or len(self.memory_id) > 200:
            raise MemoryConflictError("memory_id must be a non-empty string of at most 200 characters.")
        if not isinstance(self.version_id, str) or not self.version_id.strip() or len(self.version_id) > 200:
            raise MemoryConflictError("version_id must be a non-empty string of at most 200 characters.")
        if not isinstance(self.origin_id, str) or not self.origin_id.strip() or len(self.origin_id) > 128:
            raise MemoryConflictError("origin_id must be a non-empty string of at most 128 characters.")
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 1:
            raise MemoryConflictError("sequence must be a positive integer.")
        if not isinstance(self.text, str) or not self.text.strip() or len(self.text) > 4000:
            raise MemoryConflictError("text must contain 1-4000 characters.")
        if not isinstance(self.category, str) or self.category not in {"note", "preference", "lesson"}:
            raise MemoryConflictError("unsupported memory category.")
        if self.parent_version_id is not None and (
            not isinstance(self.parent_version_id, str) or not self.parent_version_id.strip()
        ):
            raise MemoryConflictError("parent_version_id must be a non-empty string when supplied.")
        return self


@dataclass(frozen=True)
class MemoryReconciliation:
    status: str
    memory_id: str
    versions: tuple[MemoryVersion, ...]
    conflict_id: str | None = None


def version_fingerprint(version: MemoryVersion) -> str:
    version.validate()
    payload = {
        "memory_id": version.memory_id,
        "version_id": version.version_id,
        "origin_id": version.origin_id,
        "sequence": version.sequence,
        "text": version.text,
        "category": version.category,
        "parent_version_id": version.parent_version_id,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def reconcile_memory_versions(
    local: MemoryVersion,
    incoming: MemoryVersion,
) -> MemoryReconciliation:
    """Return identical, sequential, or conflict; never silently discard a version.

    A direct child that names the current version as its parent is sequential.
    Different children of the same parent are concurrent and conflict. Without
    ancestry evidence, differing versions are conservatively treated as conflict.
    """
    local.validate()
    incoming.validate()
    if local.memory_id != incoming.memory_id:
        raise MemoryConflictError("Cannot reconcile versions for different memory IDs.")

    if version_fingerprint(local) == version_fingerprint(incoming):
        return MemoryReconciliation("identical", local.memory_id, (local,))

    if incoming.parent_version_id == local.version_id:
        return MemoryReconciliation("incoming_is_child", local.memory_id, (local, incoming))
    if local.parent_version_id == incoming.version_id:
        return MemoryReconciliation("local_is_child", local.memory_id, (incoming, local))

    pair = sorted((version_fingerprint(local), version_fingerprint(incoming)))
    conflict_id = hashlib.sha256((local.memory_id + ":" + ":".join(pair)).encode("utf-8")).hexdigest()
    versions = tuple(sorted((local, incoming), key=lambda v: (v.origin_id, v.sequence, v.version_id)))
    return MemoryReconciliation("conflict", local.memory_id, versions, conflict_id)
