"""Thread-safe, expiring one-time approvals bound to an exact invocation payload.

Storage is intentionally in-memory for this phase. Restart durability is a
separate release gate; losing pending approvals on restart fails closed.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import secrets
import threading
import time
from typing import Any


def _canonical_payload(arguments: dict[str, Any]) -> tuple[dict[str, Any], str]:
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be an object")
    try:
        encoded = json.dumps(
            arguments, ensure_ascii=False, allow_nan=False,
            sort_keys=True, separators=(",", ":"),
        )
        snapshot = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise ValueError("arguments must be JSON-safe") from exc
    return snapshot, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ActionApproval:
    approval_id: str
    skill_id: str
    risk: str
    arguments: dict[str, Any]
    payload_sha256: str
    created_at: float
    expires_at: float


class ActionApprovalStore:
    def __init__(self, max_pending: int = 128, *, ttl_seconds: int = 300, clock=time.time):
        if type(max_pending) is not int or not 1 <= max_pending <= 4096:
            raise ValueError("max_pending must be from 1 to 4096")
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 86400:
            raise ValueError("ttl_seconds must be from 1 to 86400")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self.max_pending = max_pending
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._pending: dict[str, ActionApproval] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _copy(item: ActionApproval) -> ActionApproval:
        # Round-trip JSON to ensure callers never receive the internal mutable dict.
        snapshot = json.loads(json.dumps(item.arguments, ensure_ascii=False, allow_nan=False))
        return ActionApproval(
            item.approval_id, item.skill_id, item.risk, snapshot,
            item.payload_sha256, item.created_at, item.expires_at,
        )

    def create(self, skill_id: str, arguments: dict[str, Any], *, risk: str = "high") -> ActionApproval:
        if not isinstance(skill_id, str) or not skill_id.strip():
            raise ValueError("A skill id is required.")
        if risk not in {"low", "medium", "high", "critical"}:
            raise ValueError("A valid risk level is required.")
        snapshot, digest = _canonical_payload(arguments)
        now = self._clock()
        item = ActionApproval(
            secrets.token_urlsafe(24), skill_id, risk, snapshot, digest,
            now, now + self.ttl_seconds,
        )
        with self._lock:
            self._purge_expired(now)
            if len(self._pending) >= self.max_pending:
                raise RuntimeError("Pending approval capacity reached.")
            self._pending[item.approval_id] = item
        return self._copy(item)

    def consume(self, approval_id: str, *, skill_id: str, arguments: dict[str, Any],
                risk: str) -> ActionApproval | None:
        """Atomically consume a matching, unexpired approval exactly once."""
        if not isinstance(approval_id, str) or not approval_id:
            return None
        try:
            _, digest = _canonical_payload(arguments)
        except ValueError:
            return None
        now = self._clock()
        with self._lock:
            item = self._pending.pop(approval_id, None)
            if item is None or item.expires_at <= now:
                return None
            if item.skill_id != skill_id or item.risk != risk or not secrets.compare_digest(item.payload_sha256, digest):
                return None
            return self._copy(item)

    def deny(self, approval_id: str) -> bool:
        with self._lock:
            return self._pending.pop(approval_id, None) is not None

    def _purge_expired(self, now: float) -> None:
        for approval_id, item in tuple(self._pending.items()):
            if item.expires_at <= now:
                self._pending.pop(approval_id, None)
