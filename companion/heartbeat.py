"""Proactive read-only heartbeat runner for periodic checks and reports."""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path


class HeartbeatStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS heartbeat_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )""")

    def get(self, key: str) -> str | None:
        with sqlite3.connect(self.db_path) as db:
            row = db.execute("SELECT value FROM heartbeat_state WHERE key = ?", (key,)).fetchone()
            return row[0] if row else None

    def set(self, key: str, value: str):
        with sqlite3.connect(self.db_path) as db:
            db.execute("INSERT OR REPLACE INTO heartbeat_state (key, value) VALUES (?, ?)", (key, value))


class HeartbeatRunner(threading.Thread):
    def __init__(self, workspace: Path, ai=None, reminders=None, interval_seconds=900, enabled=False):
        super().__init__(daemon=True)
        self.workspace = Path(workspace).expanduser().resolve()
        self.ai = ai
        self.reminders = reminders
        self.interval_seconds = max(0, interval_seconds)
        self.enabled = enabled
        self.store = HeartbeatStore(self.workspace / "heartbeat_state.sqlite3")
        self._stop_event = threading.Event()

    def run_check_once(self) -> dict:
        now = datetime.now(timezone.utc)
        self.store.set("last_run", now.isoformat())

        # Check due reminders
        due_reminders = []
        if self.reminders:
            due_reminders = self.reminders.due(now)

        # Check HEARTBEAT.md if present
        hb_file = self.workspace / "HEARTBEAT.md"
        reports = []
        if hb_file.exists() and self.ai:
            try:
                content = hb_file.read_text(encoding="utf-8")
                questions = [line.strip("- *").strip() for line in content.splitlines() if line.strip().startswith(("-", "*"))]
                for q in questions[:10]:
                    prompt = f"HEARTBEAT CHECK (read-only; do not request tools): {q[:500]}"
                    ans = self.ai.chat(prompt) if hasattr(self.ai, "chat") else self.ai.ask(prompt)
                    reports.append({"question": q, "answer": ans})
            except Exception:
                pass

        if reports:
            logs_dir = self.workspace / "logs"
            logs_dir.mkdir(parents=True, exist_ok=True)
            report_file = logs_dir / f"heartbeat-{now.strftime('%Y%m%d')}.md"
            with open(report_file, "a", encoding="utf-8") as handle:
                handle.write(f"\n## Heartbeat Check ({now.isoformat()})\n")
                for r in reports:
                    handle.write(f"- **Q**: {r['question']}\n  **A**: {r['answer']}\n")

        return {"time": now.isoformat(), "reminders": len(due_reminders), "checks": len(reports)}

    def run(self):
        while not self._stop_event.is_set():
            if self.enabled and self.interval_seconds > 0:
                try:
                    self.run_check_once()
                except Exception:
                    pass
                self._stop_event.wait(self.interval_seconds)
            else:
                self._stop_event.wait(5)

    def stop(self):
        self._stop_event.set()
