"""Durable approval boundary for persistent reminder mutations."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import secrets


@dataclass(frozen=True)
class ReminderMutationResult:
    ok: bool
    message: str
    data: object = None
    stage: str = "report"


class DurableReminderRuntime:
    """Persist reminder additions only from the coordinator's approved snapshot."""

    ALLOWED = {"reminder:add"}

    def __init__(self, *, reminders, coordinator, actor: str, confirm, session_id=None):
        if reminders is None or not callable(getattr(reminders, "add", None)):
            raise TypeError("A reminder queue is required.")
        if coordinator is None or not callable(getattr(coordinator, "record", None)) or not callable(getattr(coordinator, "approve", None)):
            raise TypeError("A durable approval coordinator is required.")
        if not isinstance(actor, str) or not actor.strip() or not callable(confirm):
            raise ValueError("A trusted actor and confirmation callback are required.")
        self.reminders = reminders
        self.coordinator = coordinator
        self.actor = actor
        self.confirm = confirm
        self.session_id = session_id or secrets.token_urlsafe(24)

    def add(self, text: str, due_at: datetime) -> ReminderMutationResult:
        if not isinstance(text, str) or not text.strip() or len(text) > 1000:
            return ReminderMutationResult(False, "Blocked: reminder text must be 1-1000 characters.", stage="invalid_input")
        if not isinstance(due_at, datetime) or due_at.tzinfo is None or due_at.utcoffset() is None:
            return ReminderMutationResult(False, "Blocked: reminder time must include a timezone.", stage="invalid_input")
        payload = {"text": text.strip(), "due_at": due_at.isoformat()}
        try:
            pending = self.coordinator.record(
                None, {"action": {"skill_id": "reminder:add", "arguments": payload}},
                actor=self.actor, session_id=self.session_id,
            )
            outcome = self.coordinator.approve(
                self, pending.pending_id, actor=self.actor, session_id=self.session_id,
                confirm=lambda action: self.confirm(action),
            )
        except Exception:
            return ReminderMutationResult(False, "Reminder mutation failed safely; no success was confirmed.",
                                          stage="recovery_required")
        if outcome.status != "completed":
            return ReminderMutationResult(False, f"Reminder not saved ({outcome.status}).",
                                          outcome.result, outcome.status)
        result = outcome.result
        if not isinstance(result, dict) or type(result.get("ok")) is not bool:
            return ReminderMutationResult(False, "Invalid reminder result contract.", stage="invalid_result_contract")
        return ReminderMutationResult(result["ok"], str(result.get("message", "")), result.get("data"),
                                      "report" if result["ok"] else "observe")

    def execute_approved_action(self, action: dict):
        """Coordinator executor; accepts only the stored approved action shape."""
        if not isinstance(action, dict) or set(action) != {"skill_id", "arguments"}:
            raise ValueError("Malformed approved reminder action.")
        if action["skill_id"] not in self.ALLOWED or not isinstance(action["arguments"], dict):
            raise ValueError("Unsupported approved reminder action.")
        payload = action["arguments"]
        if set(payload) != {"text", "due_at"} or not isinstance(payload["text"], str) or not isinstance(payload["due_at"], str):
            raise ValueError("Malformed approved reminder payload.")
        due_at = datetime.fromisoformat(payload["due_at"])
        if due_at.tzinfo is None or due_at.utcoffset() is None:
            raise ValueError("Approved reminder time must include a timezone.")
        item = self.reminders.add(payload["text"], due_at)
        return {"ok": True, "message": "Reminder saved.", "data": {"id": item["id"]}}
