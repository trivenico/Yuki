"""Persistent, consent-gated proactive alert deduplication.

This store records bounded operational alert metadata. It does not poll,
schedule work, send notifications, retry tasks, or authorize actions.
"""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path


_IDENTIFIER = re.compile(r"[a-z][a-z0-9_.:-]{0,63}\\Z")
_MAX_HISTORY = 500


class ProactiveAlertStore:
    """Owner-isolated alert history with atomic deduplication and cooldowns."""

    def __init__(self, path: str | Path):
        raw = Path(path).expanduser()
        if raw.is_symlink():
            raise ValueError("proactive alert database must not be a symlink.")
        raw.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = raw.parent.resolve() / raw.name
        if self.path.exists() and (self.path.is_symlink() or not self.path.is_file()):
            raise ValueError("proactive alert database must be a regular file.")
        self._initialize()

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout = 10000")
        db.execute("PRAGMA foreign_keys = ON")
        return db

    def _initialize(self):
        with closing(self._connect()) as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS proactive_alerts (
                    owner_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    category TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    message TEXT NOT NULL,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    last_notified TEXT,
                    occurrence_count INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY(owner_id, fingerprint)
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_proactive_alerts_history
                ON proactive_alerts(owner_id, last_seen DESC)
            """)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    @staticmethod
    def _validate_text(value, field, maximum):
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            raise ValueError(f"{field} must be non-empty bounded text.")
        if "\\x00" in value:
            raise ValueError(f"{field} contains invalid characters.")
        return value.strip()

    @staticmethod
    def _parse_now(now):
        value = now or datetime.now(timezone.utc)
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("now must be a timezone-aware datetime.")
        return value.astimezone(timezone.utc)

    def observe(
        self, *, owner_id: str, category: str, subject: str, message: str,
        notifications_enabled: bool, cooldown_seconds: int = 900,
        now: datetime | None = None,
    ) -> dict:
        """Record one observation and atomically decide whether it is notify-due.

        A due result means the host may attempt delivery. The timestamp is
        committed before returning, providing at-most-once notification
        attempts across concurrent observers (not guaranteed delivery).
        """
        owner_id = self._validate_text(owner_id, "owner_id", 200)
        category = self._validate_text(category, "category", 64)
        if not _IDENTIFIER.fullmatch(category):
            raise ValueError("category must be a simple lowercase identifier.")
        subject = self._validate_text(subject, "subject", 200)
        message = self._validate_text(message, "message", 500)
        if type(notifications_enabled) is not bool:
            raise ValueError("notifications_enabled must be boolean.")
        if type(cooldown_seconds) is not int or not 0 <= cooldown_seconds <= 604800:
            raise ValueError("cooldown_seconds must be between 0 and 604800.")
        observed_at = self._parse_now(now)
        timestamp = observed_at.isoformat(timespec="seconds")
        fingerprint = hashlib.sha256(
            (category + "\\0" + subject).encode("utf-8")
        ).hexdigest()
        should_notify = False

        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""
                SELECT first_seen, last_notified, occurrence_count
                FROM proactive_alerts
                WHERE owner_id = ? AND fingerprint = ?
            """, (owner_id, fingerprint)).fetchone()
            if row is None:
                first_seen = timestamp
                count = 1
                last_notified = None
            else:
                first_seen = row["first_seen"]
                count = row["occurrence_count"] + 1
                last_notified = row["last_notified"]

            due = notifications_enabled and (
                last_notified is None or
                observed_at >= datetime.fromisoformat(last_notified) +
                timedelta(seconds=cooldown_seconds)
            )
            if due:
                should_notify = True
                last_notified = timestamp

            db.execute("""
                INSERT INTO proactive_alerts
                    (owner_id, fingerprint, category, subject, message,
                     first_seen, last_seen, last_notified, occurrence_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(owner_id, fingerprint) DO UPDATE SET
                    message = excluded.message,
                    last_seen = excluded.last_seen,
                    last_notified = excluded.last_notified,
                    occurrence_count = excluded.occurrence_count
            """, (
                owner_id, fingerprint, category, subject, message,
                first_seen, timestamp, last_notified, count,
            ))
            db.execute("""
                DELETE FROM proactive_alerts
                WHERE owner_id = ? AND fingerprint IN (
                    SELECT fingerprint FROM proactive_alerts
                    WHERE owner_id = ?
                    ORDER BY last_seen DESC LIMIT -1 OFFSET ?
                )
            """, (owner_id, owner_id, _MAX_HISTORY))
            db.commit()

        return {
            "fingerprint": fingerprint,
            "category": category,
            "subject": subject,
            "message": message,
            "first_seen": first_seen,
            "last_seen": timestamp,
            "occurrence_count": count,
            "notification_due": should_notify,
        }

    def recent(self, *, owner_id: str, limit: int = 50) -> list[dict]:
        owner_id = self._validate_text(owner_id, "owner_id", 200)
        if type(limit) is not int or not 1 <= limit <= _MAX_HISTORY:
            raise ValueError("limit must be between 1 and 500.")
        with closing(self._connect()) as db:
            rows = db.execute("""
                SELECT category, subject, message, first_seen, last_seen,
                       last_notified, occurrence_count
                FROM proactive_alerts WHERE owner_id = ?
                ORDER BY last_seen DESC LIMIT ?
            """, (owner_id, limit)).fetchall()
        return [dict(row) for row in rows]
