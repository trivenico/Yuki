"""Durable local token-usage accounting without an artificial event quota.

Usage thresholds are advisory alerts only; they never block model requests. Exact
counts are recorded only when supplied by a provider. SQLite stores each event;
Python integer aggregation avoids SQLite SUM's signed-64-bit lifetime overflow.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import threading


_MAX_SQLITE_INTEGER = (1 << 63) - 1


@dataclass(frozen=True)
class UsageEvent:
    session_id: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    estimated: bool
    created_at: str


class TokenUsageTracker:
    """Append-only usage history; no daily, monthly, lifetime, or event-count quota."""

    def __init__(self, path=None, *, session_id="default", alert_threshold=100000, spike_multiplier=3.0):
        self.path = Path(path).expanduser().resolve() if path else None
        self.db_path = Path(str(self.path) + ".sqlite3") if self.path else None
        self.session_id = str(session_id)[:100]
        if isinstance(alert_threshold, bool) or not isinstance(alert_threshold, int) or alert_threshold < 1:
            raise ValueError("alert_threshold must be a positive integer")
        if isinstance(spike_multiplier, bool) or not isinstance(spike_multiplier, (int, float)) or spike_multiplier <= 1:
            raise ValueError("spike_multiplier must be greater than one")
        self.alert_threshold = alert_threshold
        self.spike_multiplier = float(spike_multiplier)
        self._lock = threading.RLock()
        if self.db_path:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(self.db_path) if self.db_path else ":memory:",
                                   timeout=30, check_same_thread=False)
        self._db.execute("PRAGMA busy_timeout = 30000")
        if self.db_path:
            self._db.execute("PRAGMA journal_mode = WAL")
            self._db.execute("PRAGMA synchronous = FULL")
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS usage_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                input_tokens INTEGER NOT NULL CHECK(input_tokens >= 0),
                output_tokens INTEGER NOT NULL CHECK(output_tokens >= 0),
                estimated INTEGER NOT NULL CHECK(estimated IN (0, 1)),
                created_at TEXT NOT NULL
            )
        """)
        self._db.execute("CREATE INDEX IF NOT EXISTS usage_events_session_id_id ON usage_events(session_id, id)")
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS usage_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        self._db.commit()
        self._migrate_legacy_json()

    def _migrate_legacy_json(self):
        """Import the previous JSON-array format once, without modifying its source."""
        if self.path is None or not self.path.is_file():
            return
        with self._lock:
            marker = self._db.execute(
                "SELECT value FROM usage_metadata WHERE key = 'legacy_json_imported'"
            ).fetchone()
            if marker:
                return
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError("Legacy usage log is unreadable; it was not overwritten.") from exc
            if not isinstance(raw, list):
                raise RuntimeError("Legacy usage log has invalid structure.")
            rows = []
            for item in raw:
                if not isinstance(item, dict):
                    raise RuntimeError("Legacy usage log contains an invalid event.")
                try:
                    event = UsageEvent(**item)
                except (TypeError, ValueError) as exc:
                    raise RuntimeError("Legacy usage log contains an invalid event.") from exc
                self._validate_event(event)
                rows.append((event.session_id, event.provider, event.model, event.input_tokens,
                             event.output_tokens, int(event.estimated), event.created_at))
            with self._db:
                self._db.executemany("""
                    INSERT INTO usage_events
                    (session_id, provider, model, input_tokens, output_tokens, estimated, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, rows)
                self._db.execute(
                    "INSERT INTO usage_metadata(key, value) VALUES ('legacy_json_imported', '1')"
                )

    @staticmethod
    def _count(value, label):
        # SQLite INTEGER is signed 64-bit. This is a storage representation bound,
        # not a product token allowance; cumulative totals use arbitrary-precision
        # Python integers and can exceed this per-row storage range.
        if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > _MAX_SQLITE_INTEGER:
            raise ValueError(f"{label} must be a non-negative integer representable by SQLite INTEGER")
        return value

    @classmethod
    def _validate_event(cls, event):
        if (not isinstance(event.provider, str) or not event.provider.strip() or len(event.provider) > 80
                or not isinstance(event.model, str) or len(event.model) > 120
                or not isinstance(event.session_id, str) or len(event.session_id) > 100
                or not isinstance(event.estimated, bool)
                or not isinstance(event.created_at, str)):
            raise RuntimeError("Usage event contains invalid fields.")
        cls._count(event.input_tokens, "input_tokens")
        cls._count(event.output_tokens, "output_tokens")

    def record(self, *, provider, model="", input_tokens, output_tokens, estimated=False):
        if not isinstance(provider, str) or not provider.strip() or len(provider) > 80:
            raise ValueError("provider must be a non-empty short string")
        if not isinstance(model, str) or len(model) > 120:
            raise ValueError("model must be a short string")
        if not isinstance(estimated, bool):
            raise ValueError("estimated must be boolean")
        input_tokens = self._count(input_tokens, "input_tokens")
        output_tokens = self._count(output_tokens, "output_tokens")
        event = UsageEvent(
            session_id=self.session_id, provider=provider.strip(), model=model,
            input_tokens=input_tokens, output_tokens=output_tokens, estimated=estimated,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        with self._lock, self._db:
            cursor = self._db.execute("""
                INSERT INTO usage_events
                (session_id, provider, model, input_tokens, output_tokens, estimated, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (event.session_id, event.provider, event.model, event.input_tokens,
                  event.output_tokens, int(event.estimated), event.created_at))
            result = {"event": asdict(event), "event_id": cursor.lastrowid, "summary": self.summary()}
        return result

    def summary(self):
        with self._lock:
            calls, exact_events, estimated_events = self._db.execute("""
                SELECT COUNT(*),
                       COALESCE(SUM(CASE WHEN estimated = 0 THEN 1 ELSE 0 END), 0),
                       COALESCE(SUM(CASE WHEN estimated = 1 THEN 1 ELSE 0 END), 0)
                FROM usage_events WHERE session_id = ?
            """, (self.session_id,)).fetchone()
            total_input = total_output = 0
            for input_count, output_count in self._db.execute("""
                SELECT input_tokens, output_tokens FROM usage_events
                WHERE session_id = ? ORDER BY id
            """, (self.session_id,)):
                total_input += input_count
                total_output += output_count
            recent = self._db.execute("""
                SELECT input_tokens, output_tokens FROM usage_events
                WHERE session_id = ? ORDER BY id DESC LIMIT 2
            """, (self.session_id,)).fetchall()
            previous = sum(recent[1]) if len(recent) >= 2 else None
            current = sum(recent[0]) if recent else 0
            total = total_input + total_output
            alerts = []
            if total >= self.alert_threshold:
                alerts.append("session_token_threshold")
            if previous and current > previous * self.spike_multiplier:
                alerts.append("usage_spike")
            return {
                "session_id": self.session_id, "calls": calls,
                "input_tokens": total_input, "output_tokens": total_output,
                "total_tokens": total, "exact_events": exact_events,
                "estimated_events": estimated_events, "alerts": alerts,
            }

    def close(self):
        with self._lock:
            self._db.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()
