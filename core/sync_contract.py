"""Validation contract for future authenticated Yuki device synchronization.

This module deliberately does not perform networking, encryption, or persistence.
It defines a small allow-listed event envelope shared by Windows and Linux clients.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
from typing import Any

MAX_EVENT_BYTES = 256 * 1024
MAX_DEPTH = 12
MAX_ITEMS = 2000
_ALLOWED_COLLECTIONS = frozenset({
    "conversation_metadata",
    "conversation_messages",
    "task_records",
    "skill_metadata",
    "agent_preferences",
    "user_preferences",
})
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$")
_SECRET_KEY_PARTS = (
    "password", "passwd", "secret", "token", "api_key", "apikey",
    "private_key", "credential", "oauth", "cookie", "authorization",
)


class SyncContractError(ValueError):
    """Raised when an event is outside Yuki's deliberately narrow sync contract."""


def _check_tree(value: Any, *, depth: int = 0, count: list[int] | None = None) -> None:
    if count is None:
        count = [0]
    count[0] += 1
    if count[0] > MAX_ITEMS:
        raise SyncContractError("Sync event contains too many values.")
    if depth > MAX_DEPTH:
        raise SyncContractError("Sync event nesting is too deep.")
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str) or not key or len(key) > 128:
                raise SyncContractError("Sync object keys must be bounded strings.")
            normalized = key.lower().replace("-", "_")
            if any(part in normalized for part in _SECRET_KEY_PARTS):
                raise SyncContractError("Credential-like fields cannot be synchronized.")
            _check_tree(child, depth=depth + 1, count=count)
    elif isinstance(value, list):
        for child in value:
            _check_tree(child, depth=depth + 1, count=count)
    elif value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, str) and len(value) > 64_000:
            raise SyncContractError("A sync text field exceeds 64 KB.")
        if isinstance(value, float):
            import math
            if not math.isfinite(value):
                raise SyncContractError("Non-finite numbers are not valid sync values.")
    else:
        raise SyncContractError("Sync payload must contain JSON-compatible values.")


@dataclass(frozen=True)
class SyncEvent:
    event_id: str
    device_id: str
    collection: str
    object_id: str
    revision: int
    occurred_at: str
    operation: str
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "device_id": self.device_id,
            "collection": self.collection,
            "object_id": self.object_id,
            "revision": self.revision,
            "occurred_at": self.occurred_at,
            "operation": self.operation,
            "payload": self.payload,
        }


def validate_sync_event(raw: Any) -> SyncEvent:
    """Validate one untrusted client event before it reaches a sync service."""
    if not isinstance(raw, dict) or set(raw) != {
        "event_id", "device_id", "collection", "object_id", "revision",
        "occurred_at", "operation", "payload",
    }:
        raise SyncContractError("Sync event has missing or unexpected fields.")
    for field in ("event_id", "device_id", "object_id"):
        value = raw[field]
        if not isinstance(value, str) or not _ID_RE.fullmatch(value):
            raise SyncContractError(f"{field} must be a bounded opaque identifier.")
    collection = raw["collection"]
    if collection not in _ALLOWED_COLLECTIONS:
        raise SyncContractError("This collection is not approved for synchronization.")
    revision = raw["revision"]
    if isinstance(revision, bool) or not isinstance(revision, int) or not 1 <= revision <= 2**63 - 1:
        raise SyncContractError("revision must be a positive 64-bit integer.")
    operation = raw["operation"]
    if operation not in {"upsert", "delete"}:
        raise SyncContractError("operation must be upsert or delete.")
    occurred_at = raw["occurred_at"]
    if not isinstance(occurred_at, str) or len(occurred_at) > 40:
        raise SyncContractError("occurred_at must be a bounded ISO-8601 timestamp.")
    try:
        parsed = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SyncContractError("occurred_at must be an ISO-8601 timestamp.") from exc
    if parsed.tzinfo is None:
        raise SyncContractError("occurred_at must include a timezone.")
    payload = raw["payload"]
    if not isinstance(payload, dict):
        raise SyncContractError("payload must be a JSON object.")
    _check_tree(payload)
    try:
        encoded = json.dumps(raw, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SyncContractError("Sync event is not valid JSON.") from exc
    if len(encoded) > MAX_EVENT_BYTES:
        raise SyncContractError("Sync event exceeds the 256 KB limit.")
    if operation == "delete" and payload:
        raise SyncContractError("Delete events must not contain a payload.")
    return SyncEvent(
        event_id=raw["event_id"],
        device_id=raw["device_id"],
        collection=collection,
        object_id=raw["object_id"],
        revision=revision,
        occurred_at=parsed.astimezone(timezone.utc).isoformat(),
        operation=operation,
        payload=payload,
    )
