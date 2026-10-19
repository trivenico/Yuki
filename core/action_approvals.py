"""One-time in-memory approval grants bound to an exact proposed action."""
import hashlib
import json
import threading


def action_fingerprint(action: dict) -> str:
    """Return a stable digest of the exact action and arguments being approved."""
    if not isinstance(action, dict):
        raise ValueError("Action must be an object.")
    try:
        encoded = json.dumps(
            action, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("Action must contain only finite JSON-compatible values.") from exc
    if len(encoded.encode("utf-8")) > 20_000:
        raise ValueError("Action exceeds the approval size limit.")
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class ActionApprovalStore:
    """Single-use grants; absent, mismatched, or reused grants are denied."""

    def __init__(self):
        self._approvals = set()
        self._lock = threading.Lock()

    def approve(self, action_id: str, approved: bool = True):
        if not isinstance(action_id, str) or not action_id:
            return False
        with self._lock:
            if approved is True:
                self._approvals.add(action_id)
            else:
                self._approvals.discard(action_id)
        return approved is True

    def is_approved(self, action_id: str) -> bool:
        with self._lock:
            return action_id in self._approvals

    def consume(self, action_id: str) -> bool:
        """Atomically consume a grant so concurrent/replayed dispatch is denied."""
        with self._lock:
            if action_id not in self._approvals:
                return False
            self._approvals.remove(action_id)
            return True
