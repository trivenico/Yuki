"""Human-in-the-loop approval. Fail closed on timeout and audit failure."""
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from core.logging_config import log_approval_audit


@dataclass(frozen=True)
class ApprovalRequest:
    action: str
    reason: str
    risk: str = "low"


class PermissionGate:
    def __init__(
        self,
        approver: Optional[Callable[[ApprovalRequest], bool]] = None,
        workspace: Optional[Path] = None,
        max_rate_limit: int = 10,
        rate_window_seconds: int = 300,
        timeout_seconds: int = 120,
    ):
        if type(max_rate_limit) is not int or max_rate_limit < 1:
            raise ValueError("max_rate_limit must be a positive integer")
        if type(rate_window_seconds) not in (int, float) or rate_window_seconds <= 0:
            raise ValueError("rate_window_seconds must be positive")
        if type(timeout_seconds) not in (int, float) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._approver = approver
        self.workspace = Path(workspace) if workspace is not None else None
        self.max_rate_limit = max_rate_limit
        self.rate_window_seconds = rate_window_seconds
        self.timeout_seconds = timeout_seconds
        self._request_timestamps: List[float] = []
        self._state_lock = threading.Lock()
        self._approver_running = False

    def _audit(self, action: str, reason: str, risk: str, decision: bool) -> bool:
        if self.workspace is None:
            return True
        try:
            log_approval_audit(self.workspace, action, reason, risk, decision)
            return True
        except OSError:
            # Audit persistence is part of the approval decision. Never allow an
            # approval through if it cannot be durably recorded.
            return False

    def request(self, action: str, reason: str, risk: str = "low") -> bool:
        if not isinstance(action, str) or not action.strip():
            return False
        if not isinstance(reason, str) or not reason.strip():
            return False
        if risk not in {"low", "medium", "high"}:
            return False

        now = time.monotonic()
        with self._state_lock:
            self._request_timestamps = [
                ts for ts in self._request_timestamps
                if now - ts < self.rate_window_seconds
            ]
            if len(self._request_timestamps) >= self.max_rate_limit:
                # Audit outside the state lock below.
                rate_limited = True
            else:
                rate_limited = False
            if not rate_limited:
                # Rate-limit every request, including denials and timeouts.
                self._request_timestamps.append(now)
            if self._approver is None or rate_limited:
                decision = False
            elif self._approver_running:
                # A previous timed-out callback is still executing. Do not create
                # unbounded worker threads or issue concurrent approval prompts.
                decision = False
            else:
                decision = None
                self._approver_running = True

        if decision is None:
            completed = threading.Event()
            outcome = {"approved": False}

            def run_approver():
                try:
                    outcome["approved"] = (
                        self._approver(ApprovalRequest(action=action, reason=reason, risk=risk)) is True
                    )
                except Exception:
                    outcome["approved"] = False
                finally:
                    with self._state_lock:
                        self._approver_running = False
                    completed.set()

            threading.Thread(
                target=run_approver,
                name="yuki-approval-callback",
                daemon=True,
            ).start()
            if completed.wait(self.timeout_seconds):
                decision = outcome["approved"]
            else:
                decision = False

        # Fail closed on audit write errors for approvals and denials alike.
        audit_ok = self._audit(action, reason, risk, decision)
        return decision and audit_ok
