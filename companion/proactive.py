"""Local reminder queue backed by atomic, cross-process SQLite claims."""
from __future__ import annotations

from datetime import datetime, timezone, date, timedelta
from pathlib import Path
from uuid import uuid4

from companion.state_store import CompanionStateStore


class ReminderQueue:
    MAX_REMINDERS = 1000
    MAX_TEXT = 1000

    def __init__(self, path: str | Path = "yuki_companion.sqlite3", *, owner_id: str = "local",
                 state_store: CompanionStateStore | None = None):
        self.store = state_store or CompanionStateStore(path, owner_id=owner_id)

    def add(self, text: str, due_at: datetime) -> dict:
        if not isinstance(text, str) or not text.strip() or len(text) > self.MAX_TEXT:
            raise ValueError(f"Reminder text must be 1-{self.MAX_TEXT} characters.")
        if not isinstance(due_at, datetime) or due_at.tzinfo is None or due_at.utcoffset() is None:
            raise ValueError("due_at must be timezone-aware.")
        item = {"id": uuid4().hex, "text": text.strip(),
                "due_at": due_at.astimezone(timezone.utc).isoformat(), "delivered": False}
        return self.store.add_record("reminder", item)

    def claim_due_notifications(
        self, now: datetime | None = None, *, lease_seconds: int = 60, limit: int = 20,
    ) -> list[dict]:
        """Claim durable notification work; delivery is not implied by claiming."""
        current = now or datetime.now(timezone.utc)
        self.store.enqueue_due_reminders(current)
        return self.store.claim_outbox(current, lease_seconds=lease_seconds, limit=limit)

    def complete_notification(self, claim: dict, now: datetime | None = None) -> bool:
        if not isinstance(claim, dict):
            return False
        return self.store.complete_outbox(
            claim.get("event_id"), claim.get("lease_token"), now or datetime.now(timezone.utc)
        )

    def fail_notification(
        self, claim: dict, error: str, now: datetime | None = None, *,
        retry_after_seconds: int = 60, max_attempts: int = 8,
    ) -> bool:
        if not isinstance(claim, dict):
            return False
        return self.store.fail_outbox(
            claim.get("event_id"), claim.get("lease_token"), now or datetime.now(timezone.utc),
            error=error, retry_after_seconds=retry_after_seconds, max_attempts=max_attempts,
        )

    def due(self, now: datetime | None = None) -> list[dict]:
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware.")
        return self.store.claim_due_reminders(now)

    def list(self) -> list[dict]:
        return self.store.list_records("reminder")


def summarize_habit_streaks(entries, *, today=None):
    """Summarize explicitly logged habit entries; never infer unrecorded behavior."""
    if not isinstance(entries, (list, tuple)):
        raise ValueError("entries must be a list of health log records.")
    current = today or datetime.now(timezone.utc).date()
    if isinstance(current, datetime):
        current = current.date()
    if not isinstance(current, date):
        raise ValueError("today must be a date.")
    dates_by_habit = {}
    for item in entries:
        if not isinstance(item, dict) or item.get("type") not in {"habit", "exercise"}:
            continue
        detail, stamp = item.get("detail"), item.get("occurred_at")
        if not isinstance(detail, str) or not detail.strip() or not isinstance(stamp, str):
            continue
        try:
            day = datetime.fromisoformat(stamp).date()
        except ValueError:
            continue
        dates_by_habit.setdefault(detail.strip().casefold(), {"label": detail.strip(), "dates": set()})["dates"].add(day)
    summaries = []
    for item in dates_by_habit.values():
        dates = item["dates"]
        streak, cursor = 0, current
        while cursor in dates:
            streak += 1
            cursor -= timedelta(days=1)
        summaries.append({"habit": item["label"], "logged_days": len(dates), "current_streak_days": streak,
                          "last_logged_date": max(dates).isoformat() if dates else None,
                          "source": "explicit_user_logs"})
    return sorted(summaries, key=lambda x: (-x["current_streak_days"], -x["logged_days"], x["habit"].casefold()))
