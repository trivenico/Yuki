"""Human-in-the-loop approval. Fail closed: no callback means no approval."""
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass(frozen=True)
class ApprovalRequest:
    action: str
    reason: str
    risk: str = "low"


class PermissionGate:
    def __init__(self, approver: Optional[Callable[[ApprovalRequest], bool]] = None):
        self._approver = approver

    def request(self, action: str, reason: str, risk: str = "low") -> bool:
        if not isinstance(action, str) or not action.strip():
            return False
        if not isinstance(reason, str) or not reason.strip():
            return False
        if not isinstance(risk, str) or risk not in {"low", "medium", "high"}:
            return False
        if self._approver is None:
            return False
        try:
            return self._approver(ApprovalRequest(action=action, reason=reason, risk=risk)) is True
        except Exception:
            return False
