"""Durable, actor-bound consent and reviewer approval backed by the audit chain."""
from __future__ import annotations

import re

from core.approval_audit import SQLiteApprovalAuditLog


class LearningAuthorizationError(RuntimeError):
    """Raised when durable learning authorization cannot be recorded or verified."""


class LearningAuthorizationLedger:
    """Record consent and per-example approvals for one authenticated desktop actor.

    Construct this object only after the desktop session has authenticated.
    The actor ID is captured at construction, never accepted from UI input on
    individual consent/approval actions. The underlying audit log stores hashes
    of actor and correlation IDs, not message content or transcripts.
    """

    PURPOSES = frozenset({"learning", "training"})
    CONSENT_SKILL_PREFIX = "yuki.learning.consent."
    EXAMPLE_SKILL = "yuki.learning.example"

    def __init__(self, audit_log: SQLiteApprovalAuditLog, *, actor_id: str) -> None:
        if not isinstance(audit_log, SQLiteApprovalAuditLog):
            raise TypeError("audit_log must be a trusted SQLiteApprovalAuditLog.")
        if not isinstance(actor_id, str) or not actor_id.strip() or len(actor_id) > 256:
            raise ValueError("actor_id must be bounded authenticated identity text.")
        self._audit = audit_log
        self._actor_id = actor_id

    def record_consent(self, purpose: str, enabled: bool) -> int:
        """Persist an explicit user choice; disabling consent revokes future use."""
        if purpose not in self.PURPOSES:
            raise ValueError("purpose must be learning or training.")
        if type(enabled) is not bool:
            raise ValueError("enabled must be a boolean.")
        try:
            return self._audit.append(
                event_type="approved" if enabled else "denied",
                pending_id=f"consent:{purpose}",
                actor=self._actor_id,
                skill_id=self.CONSENT_SKILL_PREFIX + purpose,
            )
        except Exception as exc:
            raise LearningAuthorizationError("The consent decision could not be durably audited.") from exc

    def has_consent(self, purpose: str) -> bool:
        """Return current consent only if the complete audit chain verifies."""
        if purpose not in self.PURPOSES:
            return False
        try:
            latest = self._audit.latest_event(
                pending_id=f"consent:{purpose}",
                actor=self._actor_id,
                skill_id=self.CONSENT_SKILL_PREFIX + purpose,
            )
            return latest == "approved"
        except Exception:
            return False

    @staticmethod
    def _validate_proposal_id(proposal_id: str) -> None:
        if not isinstance(proposal_id, str) or not re.fullmatch(r"[a-f0-9]{24}", proposal_id):
            raise ValueError("proposal_id must be a 24-character lowercase hexadecimal identifier.")

    def record_example_approval(self, proposal_id: str, approved: bool) -> int:
        """Record an explicit reviewer decision for one exact staged proposal."""
        self._validate_proposal_id(proposal_id)
        if type(approved) is not bool:
            raise ValueError("approved must be a boolean.")
        try:
            return self._audit.append(
                event_type="approved" if approved else "denied",
                pending_id=proposal_id,
                actor=self._actor_id,
                skill_id=self.EXAMPLE_SKILL,
            )
        except Exception as exc:
            raise LearningAuthorizationError("The example review decision could not be durably audited.") from exc

    def verify_example_approval(self, proposal_id: str, approved_by: str) -> bool:
        """Verify latest non-revoked approval by this same authenticated actor."""
        try:
            self._validate_proposal_id(proposal_id)
            if approved_by != self._actor_id:
                return False
            latest = self._audit.latest_event(
                pending_id=proposal_id,
                actor=self._actor_id,
                skill_id=self.EXAMPLE_SKILL,
            )
            return latest == "approved"
        except Exception:
            return False

    @property
    def actor_id(self) -> str:
        """Authenticated actor bound to this ledger instance."""
        return self._actor_id
