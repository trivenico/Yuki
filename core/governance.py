"""Small governance primitives for reviewed, auditable agent actions."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
from typing import Callable


@dataclass(frozen=True)
class GovernanceDecision:
    action_id: str
    action: str
    actor: str
    approved: bool
    reason: str
    timestamp: str


class Governance:
    """Fail-closed approval facade with an append-only JSONL decision log."""

    def __init__(self, approver: Callable[[str, str], bool] | None = None,
                 audit_path: str | Path | None = None):
        self._approver = approver
        self._audit_path = Path(audit_path).expanduser() if audit_path else None
        self._lock = threading.RLock()
        self._decisions: list[GovernanceDecision] = []

    def request(self, *, action_id: str, action: str, actor: str, reason: str) -> GovernanceDecision:
        if not all(isinstance(x, str) and x.strip() for x in (action_id, action, actor, reason)):
            raise ValueError("action_id, action, actor, and reason are required")
        try:
            approved = self._approver is not None and bool(self._approver(action, reason))
        except Exception:
            approved = False
        decision = GovernanceDecision(action_id[:120], action[:200], actor[:120], approved,
                                      reason[:1000], datetime.now(timezone.utc).isoformat())
        with self._lock:
            self._decisions.append(decision)
            if self._audit_path:
                self._audit_path.parent.mkdir(parents=True, exist_ok=True)
                with self._audit_path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(asdict(decision), ensure_ascii=False) + "\n")
        return decision

    def decisions(self) -> list[GovernanceDecision]:
        with self._lock:
            return list(self._decisions)
