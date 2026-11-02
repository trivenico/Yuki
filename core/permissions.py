"""Human-in-the-loop approval. Fail closed: no callback means no approval."""
from dataclasses import dataclass
import secrets
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

    def _audit(self, event_type: str, receipt_id: str, skill_id: str) -> bool:
        """Write metadata only; a configured but unavailable audit fails closed."""
        if self._audit_log is None:
            return True
        try:
            self._audit_log.append(
                event_type=event_type,
                pending_id=receipt_id,
                actor="runtime",
                skill_id=skill_id,
            )
            return True
        except Exception:
            return False

    def request(self, action: str, reason: str, risk: str = "low") -> bool:
        approved, _receipt_id = self.request_with_receipt(action, reason, risk)
        return approved

    def request_with_receipt(
        self, action: str, reason: str, risk: str = "low", *, skill_id: str = "permission_gate"
    ) -> tuple[bool, str | None]:
        """Authorize one action and return a unique correlation receipt for its audit trail."""
        if not isinstance(action, str) or not action.strip():
            return False, None
        if not isinstance(reason, str) or not reason.strip():
            return False, None
        if not isinstance(risk, str) or risk not in {"low", "medium", "high"}:
            return False, None
        if not isinstance(skill_id, str) or not skill_id.strip() or len(skill_id) > 120:
            return False, None
        if self._approver is None:
            return False, None
        receipt_id = secrets.token_urlsafe(24)
        if not self._audit("requested", receipt_id, skill_id):
            return False, None
        try:
            approved = self._approver(ApprovalRequest(action=action, reason=reason, risk=risk)) is True
        except Exception:
            approved = False
        if not self._audit("approved" if approved else "denied", receipt_id, skill_id):
            return False, None
        return approved, receipt_id if approved else None

    def record_execution(self, receipt_id: str, *, skill_id: str, succeeded: bool) -> bool:
        """Append the terminal execution event for an approved runtime receipt."""
        if not isinstance(receipt_id, str) or not receipt_id.strip() or len(receipt_id) > 128:
            return False
        if not isinstance(skill_id, str) or not skill_id.strip() or len(skill_id) > 120:
            return False
        if type(succeeded) is not bool:
            return False
        return self._audit("executed" if succeeded else "execution_failed", receipt_id, skill_id)
