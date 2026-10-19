"""Human-in-the-loop approval. Fail closed: no callback means no approval."""
from dataclasses import dataclass
import hashlib
import json
import secrets
import threading
import time
from typing import Callable, Optional


MAX_RECEIPTS = 1000
RECEIPT_TTL_SECONDS = 120


@dataclass(frozen=True)
class ApprovalRequest:
    action: str
    reason: str
    risk: str = "low"


@dataclass(frozen=True)
class ApprovalReceipt:
    """Opaque, short-lived authorization minted by one PermissionGate."""
    token: str
    action_fingerprint: str


def _fingerprint(action_snapshot: dict) -> str:
    try:
        encoded = json.dumps(
            action_snapshot, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("Approval action must be strict JSON.") from exc
    if len(encoded.encode("utf-8")) > 20_000:
        raise ValueError("Approval action exceeds the size limit.")
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class PermissionGate:
    def __init__(self, approver: Optional[Callable[[ApprovalRequest], bool]] = None):
        self._approver = approver
        self._receipts = {}
        self._lock = threading.Lock()

    def request(self, action: str, reason: str, risk: str = "low") -> bool:
        if not isinstance(action, str) or not action.strip():
            return False
        if not isinstance(reason, str) or not reason.strip():
            return False
        if risk not in {"low", "medium", "high"}:
            return False
        if self._approver is None:
            return False
        try:
            return self._approver(ApprovalRequest(action=action, reason=reason, risk=risk)) is True
        except Exception:
            return False

    def issue_receipt(self, action_snapshot: dict, confirm: Callable[[dict], bool],
                      *, risk: str = "low") -> Optional[ApprovalReceipt]:
        """Ask a human to approve an exact action and issue a one-use receipt."""
        if not callable(confirm) or not isinstance(action_snapshot, dict):
            return None
        try:
            fingerprint = _fingerprint(action_snapshot)
            import copy
            approved = confirm(copy.deepcopy(action_snapshot))
        except Exception:
            return None
        if approved is not True:
            return None
        now = time.monotonic()
        with self._lock:
            expired = [token for token, item in self._receipts.items() if item[1] <= now]
            for token in expired:
                del self._receipts[token]
            if len(self._receipts) >= MAX_RECEIPTS:
                return None
            token = secrets.token_urlsafe(32)
            self._receipts[token] = (fingerprint, now + RECEIPT_TTL_SECONDS)
        return ApprovalReceipt(token=token, action_fingerprint=fingerprint)

    def consume_receipt(self, receipt: ApprovalReceipt, action_snapshot: dict) -> bool:
        """Consume a receipt only once and only for the exact approved action."""
        if not isinstance(receipt, ApprovalReceipt) or not isinstance(action_snapshot, dict):
            return False
        try:
            fingerprint = _fingerprint(action_snapshot)
        except ValueError:
            return False
        now = time.monotonic()
        with self._lock:
            expired = [token for token, item in self._receipts.items() if item[1] <= now]
            for token in expired:
                del self._receipts[token]
            stored = self._receipts.pop(receipt.token, None)
        return stored is not None and stored[1] > now and stored[0] == fingerprint == receipt.action_fingerprint
