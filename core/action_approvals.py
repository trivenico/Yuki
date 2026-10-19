"""Bounded, expiring, one-time grants bound to an exact proposed action."""
import hashlib
import json
import threading
import time


MAX_ACTION_BYTES = 20_000
MAX_APPROVALS = 1_000
DEFAULT_TTL_SECONDS = 120


def _validate_json_value(value, depth=0):
    """Reject values JSON would silently coerce or encode non-portably."""
    if depth > 64:
        raise ValueError("Action nesting exceeds the supported depth.")
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float:
        return
    if type(value) is list:
        for item in value:
            _validate_json_value(item, depth + 1)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError("Action object keys must be strings.")
            _validate_json_value(item, depth + 1)
        return
    raise ValueError("Action contains a non-JSON value.")


def validate_strict_json(value):
    """Validate JSON values without coercing Python-specific types."""
    _validate_json_value(value)


def action_fingerprint(action: dict) -> str:
    """Return a stable digest of a finite, strict JSON action."""
    if type(action) is not dict:
        raise ValueError("Action must be an object.")
    try:
        _validate_json_value(action)
        encoded = json.dumps(
            action, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("Action must contain only finite strict-JSON values.") from exc
    if len(encoded.encode("utf-8")) > MAX_ACTION_BYTES:
        raise ValueError("Action exceeds the approval size limit.")
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class ActionApprovalStore:
    """Single-use grants with a short TTL and bounded in-memory capacity."""

    def __init__(self, *, ttl_seconds: int = DEFAULT_TTL_SECONDS, max_approvals: int = MAX_APPROVALS):
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 600:
            raise ValueError("ttl_seconds must be between 1 and 600.")
        if type(max_approvals) is not int or not 1 <= max_approvals <= MAX_APPROVALS:
            raise ValueError("max_approvals must be between 1 and 1000.")
        self.ttl_seconds = ttl_seconds
        self.max_approvals = max_approvals
        self._approvals = {}
        self._lock = threading.Lock()

    def _purge_expired(self, now):
        expired = [key for key, expiry in self._approvals.items() if expiry <= now]
        for key in expired:
            del self._approvals[key]

    def approve(self, action_id: str, approved: bool = True):
        if not isinstance(action_id, str) or not action_id:
            return False
        now = time.monotonic()
        with self._lock:
            self._purge_expired(now)
            if approved is not True:
                self._approvals.pop(action_id, None)
                return False
            if action_id not in self._approvals and len(self._approvals) >= self.max_approvals:
                return False
            self._approvals[action_id] = now + self.ttl_seconds
            return True

    def is_approved(self, action_id: str) -> bool:
        now = time.monotonic()
        with self._lock:
            self._purge_expired(now)
            return action_id in self._approvals

    def consume(self, action_id: str) -> bool:
        """Atomically consume a non-expired grant; replays and expired grants deny."""
        now = time.monotonic()
        with self._lock:
            self._purge_expired(now)
            if action_id not in self._approvals:
                return False
            del self._approvals[action_id]
            return True
