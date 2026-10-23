"""Approval-gated recovery proposals for Yuki.

This module never executes recovery actions. Trusted callers register recovery
callbacks; proposing a recovery is read-only, and execution requires a fresh
explicit approval callback. Callbacks should implement their own narrow scope,
timeouts, backup and rollback requirements.
"""
from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Callable


@dataclass(frozen=True)
class RecoveryProposal:
    action: str
    reason: str
    risk: str = "medium"


@dataclass(frozen=True)
class RecoveryResult:
    ok: bool
    status: str
    message: str
    action: str


class SelfHealingEngine:
    """A registry and approval gate, not an autonomous mutation engine."""

    MAX_ACTIONS = 50
    MAX_FIELD = 300
    ALLOWED_RISKS = frozenset({"low", "medium", "high", "critical"})

    def __init__(self, approve: Callable[[RecoveryProposal], bool] | None = None):
        self._actions: dict[str, tuple[str, Callable[[], object]]] = {}
        self._approve = approve
        self._lock = RLock()

    def register(self, name: str, callback: Callable[[], object], *, risk: str = "medium") -> None:
        if not isinstance(name, str) or not name.strip() or len(name) > 80:
            raise ValueError("Action name must be non-empty and at most 80 characters.")
        if risk not in self.ALLOWED_RISKS:
            raise ValueError("Unsupported risk level.")
        if not callable(callback):
            raise TypeError("Recovery callback must be callable.")
        with self._lock:
            if name in self._actions:
                raise ValueError("Recovery action is already registered.")
            if len(self._actions) >= self.MAX_ACTIONS:
                raise ValueError("Recovery action limit reached.")
            self._actions[name] = (risk, callback)

    def propose(self, name: str, reason: str) -> RecoveryProposal:
        if not isinstance(reason, str) or not reason.strip() or len(reason) > self.MAX_FIELD:
            raise ValueError("Reason must be non-empty and bounded.")
        with self._lock:
            item = self._actions.get(name)
        if item is None:
            raise KeyError("Recovery action is not registered.")
        return RecoveryProposal(name, reason.strip(), item[0])

    def execute_approved(self, proposal: RecoveryProposal) -> RecoveryResult:
        if not isinstance(proposal, RecoveryProposal):
            return RecoveryResult(False, "blocked", "Invalid recovery proposal.", "")
        with self._lock:
            item = self._actions.get(proposal.action)
        if item is None or item[0] != proposal.risk:
            return RecoveryResult(False, "blocked", "Proposal does not match a registered action.", proposal.action[:80])
        if self._approve is None:
            return RecoveryResult(False, "approval_required", "No approval provider is configured.", proposal.action)
        try:
            approved = self._approve(proposal)
        except Exception:
            return RecoveryResult(False, "approval_error", "Approval provider failed; action was not executed.", proposal.action)
        if approved is not True:
            return RecoveryResult(False, "denied", "Recovery was not approved.", proposal.action)
        try:
            result = item[1]()
            if result is True or (isinstance(result, dict) and result.get("ok") is True):
                return RecoveryResult(True, "completed", "Recovery callback reported success.", proposal.action)
            return RecoveryResult(False, "failed", "Recovery callback did not report success.", proposal.action)
        except Exception as exc:
            return RecoveryResult(False, "failed", f"Recovery callback raised {type(exc).__name__}.", proposal.action)
