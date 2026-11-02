"""Human-in-the-loop approval. Fail closed: no callback means no approval."""
from dataclasses import dataclass
import hashlib
from typing import Callable, Optional


@dataclass(frozen=True)
class ApprovalRequest:
    action: str
    reason: str
    risk: str = "low"


class PermissionGate:
    def __init__(self, approver: Optional[Callable[[ApprovalRequest], bool]] = None, audit_log=None):
        self._approver = approver
        self._audit_log = audit_log

    def _audit(self, event_type: str, fingerprint: str) -> bool:
        """Write metadata only; a configured but unavailable audit fails closed."""
        if self._audit_log is None:
            return True
        try:
            self._audit_log.append(
                event_type=event_type,
                pending_id=fingerprint,
                actor="runtime",
                skill_id="permission_gate",
            )
            return True
        except Exception:
            return False

    def request(self, action: str, reason: str, risk: str = "low") -> bool:
        if not isinstance(action, str) or not action.strip():
            return False
        if not isinstance(reason, str) or not reason.strip():
            return False
        if not isinstance(risk, str) or risk not in {"low", "medium", "high"}:
            return False
        if self._approver is None:
            return False
        # Persist only a one-way fingerprint, never the action or approval reason.
        fingerprint = hashlib.sha256(action.encode("utf-8")).hexdigest()
        if not self._audit("requested", fingerprint):
            return False
        try:
            approved = self._approver(ApprovalRequest(action=action, reason=reason, risk=risk)) is True
        except Exception:
            approved = False
        if not self._audit("approved" if approved else "denied", fingerprint):
            return False
        return approved
