"""Human-in-the-loop approval. Fail closed: no callback means no approval."""
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, List
from core.logging_config import log_approval_audit


@dataclass(frozen=True)
class ApprovalRequest:
    action: str
    reason: str
    risk: str = "low"


class PermissionGate:
    def __init__(self, approver: Optional[Callable[[ApprovalRequest], bool]] = None, workspace: Optional[Path] = None, max_rate_limit: int = 10, rate_window_seconds: int = 300, timeout_seconds: int = 120):
        self._approver = approver
        self.workspace = workspace
        self.max_rate_limit = max_rate_limit
        self.rate_window_seconds = rate_window_seconds
        self.timeout_seconds = timeout_seconds
        self._approval_timestamps: List[float] = []

    def request(self, action: str, reason: str, risk: str = "low") -> bool:
        now = time.time()
        # Clean timestamps older than window
        self._approval_timestamps = [ts for ts in self._approval_timestamps if now - ts < self.rate_window_seconds]
        if len(self._approval_timestamps) >= self.max_rate_limit:
            if self.workspace:
                log_approval_audit(self.workspace, action, reason, risk, False)
            return False

        if not isinstance(action, str) or not action.strip():
            return False
        if not isinstance(reason, str) or not reason.strip():
            return False
        if risk not in {"low", "medium", "high"}:
            return False
        if self._approver is None:
            decision = False
        else:
            try:
                decision = self._approver(ApprovalRequest(action=action, reason=reason, risk=risk)) is True
            except Exception:
                decision = False

        if decision:
            self._approval_timestamps.append(time.time())

        if self.workspace:
            log_approval_audit(self.workspace, action, reason, risk, decision)

        return decision
