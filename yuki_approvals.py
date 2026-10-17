"""In-memory human-in-the-loop gate for consequential actions.

This module records approval decisions; it does not execute the proposed action.
A production implementation must persist requests safely and re-authorize at execution time.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

MAX_ACTION_TEXT = 500


@dataclass
class ApprovalRequest:
    id: str
    action: str
    rationale: str
    status: str
    created_at: str


class ApprovalGate:
    def __init__(self):
        self._requests = {}

    def request(self, action, rationale):
        if not isinstance(action, str) or not action.strip():
            raise ValueError("Action description is required.")
        if not isinstance(rationale, str) or not rationale.strip():
            raise ValueError("A rationale is required.")
        request = ApprovalRequest(
            id=uuid4().hex[:12],
            action=action.strip()[:MAX_ACTION_TEXT],
            rationale=rationale.strip()[:MAX_ACTION_TEXT],
            status="pending",
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._requests[request.id] = request
        return request

    def decide(self, request_id, approved):
        if not isinstance(approved, bool):
            raise ValueError("Decision must be true (approve) or false (reject).")
        request = self._requests.get(request_id)
        if request is None:
            return "Approval request not found."
        if request.status != "pending":
            return f"Request already {request.status}."
        request.status = "approved" if approved else "rejected"
        return f"Request {request.id} {request.status}. No action was executed."

    def list_pending(self):
        return [request for request in self._requests.values() if request.status == "pending"]
