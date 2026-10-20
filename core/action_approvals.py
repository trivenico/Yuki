"""One-time in-memory approval state."""

from dataclasses import dataclass
import json
import secrets


@dataclass(frozen=True)
class ActionApproval:
    approval_id: str
    skill_id: str
    arguments: dict


class ActionApprovalStore:
    def __init__(self, max_pending=128):
        if type(max_pending) is not int or not 1 <= max_pending <= 4096:
            raise ValueError("max_pending must be from 1 to 4096")
        self.max_pending = max_pending
        self._pending = {}

    def create(self, skill_id, arguments):
        if len(self._pending) >= self.max_pending:
            raise RuntimeError("Pending approval capacity reached.")
        if not isinstance(skill_id, str) or not skill_id or not isinstance(arguments, dict):
            raise ValueError("A skill id and object arguments are required.")
        try:
            snapshot = json.loads(json.dumps(arguments, ensure_ascii=False, allow_nan=False))
        except (TypeError, ValueError) as exc:
            raise ValueError("arguments must be JSON-safe") from exc
        item = ActionApproval(secrets.token_urlsafe(24), skill_id, snapshot)
        self._pending[item.approval_id] = item
        return item

    def consume(self, approval_id):
        return self._pending.pop(approval_id, None)

    def deny(self, approval_id):
        return self._pending.pop(approval_id, None) is not None
