"""Local reminder queue: emits due reminders, never runs arbitrary actions."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from uuid import uuid4


class ReminderQueue:
    MAX_REMINDERS = 1000
    MAX_TEXT = 1000

    def __init__(self, path: str | Path = "yuki_reminders.json"):
        self.path = Path(path).expanduser().resolve()
        self._lock = RLock()

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("Reminder store is unreadable; no data was overwritten.") from exc
        if not isinstance(data, list) or len(data) > self.MAX_REMINDERS:
            raise RuntimeError("Reminder store has an invalid structure or exceeds limits.")
        return data

    def _save(self, items):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".yuki-reminders-", suffix=".tmp", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(items[-self.MAX_REMINDERS:], handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass

    def add(self, text: str, due_at: datetime) -> dict:
        if not isinstance(text, str) or not text.strip() or len(text) > self.MAX_TEXT:
            raise ValueError(f"Reminder text must be 1-{self.MAX_TEXT} characters.")
        if not isinstance(due_at, datetime) or due_at.tzinfo is None or due_at.utcoffset() is None:
            raise ValueError("due_at must be timezone-aware.")
        with self._lock:
            items = self._load()
            if len(items) >= self.MAX_REMINDERS:
                raise ValueError("Reminder queue is full.")
            item = {"id": uuid4().hex, "text": text.strip(),
                    "due_at": due_at.astimezone(timezone.utc).isoformat(), "delivered": False}
            items.append(item)
            self._save(items)
            return dict(item)

    def due(self, now: datetime | None = None) -> list[dict]:
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware.")
        with self._lock:
            items = self._load()
            ready = []
            for item in items:
                if not item.get("delivered") and datetime.fromisoformat(item["due_at"]) <= now:
                    ready.append(dict(item))
                    item["delivered"] = True
            if ready:
                self._save(items)
            return ready

    def list(self) -> list[dict]:
        with self._lock:
            return [dict(item) for item in self._load()]
