"""Operation-bound, one-time approvals for consequential tool calls."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import secrets
import threading
from typing import Any


def _canonical_action(action: dict[str, Any]) -> str:
    try:
        encoded = json.dumps(action, ensure_ascii=False, allow_nan=False, sort_keys=True,
                             separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Approval action must be finite JSON-compatible data.") from exc
    if len(encoded.encode("utf-8")) > 16 * 1024:
        raise ValueError("Approval action exceeds 16 KiB.")
    return encoded


@dataclass(frozen=True)
class ApprovalToken:
    token: str
    action_hash: str
    actor: str
    session_id: str
    expires_at: str


class ActionApprovalStore:
    """In-memory, one-time approvals bound to exact canonical action data.

    Restart clears approvals by design. For multi-process deployments, replace with
    a transactional shared store and authenticated actor/session identity.
    """

    def __init__(self, *, ttl_seconds: int = 120):
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 900:
            raise ValueError("ttl_seconds must be between 1 and 900.")
        self.ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._tokens: dict[str, ApprovalToken] = {}

    def issue(self, action: dict[str, Any], *, actor: str, session_id: str,
              approved: bool) -> ApprovalToken:
        if approved is not True:
            raise PermissionError("Approval was not explicitly granted.")
        if not isinstance(actor, str) or not actor.strip() or len(actor) > 120:
            raise ValueError("actor must be non-empty and bounded.")
        if not isinstance(session_id, str) or not session_id.strip() or len(session_id) > 120:
            raise ValueError("session_id must be non-empty and bounded.")
        canonical = _canonical_action(action)
        now = datetime.now(timezone.utc)
        item = ApprovalToken(
            token=secrets.token_urlsafe(32),
            action_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            actor=actor.strip(),
            session_id=session_id.strip(),
            expires_at=(now + timedelta(seconds=self.ttl_seconds)).isoformat(),
        )
        with self._lock:
            self._purge(now)
            self._tokens[item.token] = item
        return item

    def consume(self, token: str, action: dict[str, Any], *, actor: str, session_id: str) -> bool:
        if not isinstance(token, str) or not token or not isinstance(actor, str) or not isinstance(session_id, str):
            return False
        try:
            canonical = _canonical_action(action)
        except ValueError:
            return False
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        now = datetime.now(timezone.utc)
        with self._lock:
            item = self._tokens.pop(token, None)
            self._purge(now)
            if item is None:
                return False
            if item.expires_at <= now.isoformat():
                return False
            return (secrets.compare_digest(item.action_hash, digest)
                    and secrets.compare_digest(item.actor, actor)
                    and secrets.compare_digest(item.session_id, session_id))

    def _purge(self, now: datetime) -> None:
        cutoff = now.isoformat()
        expired = [key for key, item in self._tokens.items() if item.expires_at <= cutoff]
        for key in expired:
            self._tokens.pop(key, None)
