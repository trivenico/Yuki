"""Owner-scoped durable reminders for the Yuki companion.

Reminders are inert data: this store never executes commands or arbitrary actions.
The internal dispatcher methods implement leased, at-least-once delivery claims;
they are not exposed through the user-facing API.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from uuid import uuid4
from typing import Callable


class CompanionReminderStore:
    MAX_TEXT = 1000
    MAX_PER_OWNER = 1000
    MAX_DELIVERY_ATTEMPTS = 5
    MAX_RETRY_DELAY_SECONDS = 3600

    def __init__(self, database_path: str | Path, *, authorize: Callable[[str, str], bool]):
        if not callable(authorize):
            raise TypeError("authorize must be callable")
        self.database_path = str(Path(database_path).expanduser().resolve())
        self.authorize = authorize
        self._lock = RLock()
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS companion_reminders (
                    reminder_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    due_at TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending','cancelled','delivered')),
                    created_at TEXT NOT NULL,
                    lease_token TEXT,
                    lease_until TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT,
                    delivered_at TEXT,
                    exhausted_at TEXT
                )
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(companion_reminders)")}
            migrations = {
                "lease_token": "TEXT", "lease_until": "TEXT",
                "attempts": "INTEGER NOT NULL DEFAULT 0", "next_attempt_at": "TEXT",
                "delivered_at": "TEXT", "exhausted_at": "TEXT",
            }
            for name, declaration in migrations.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE companion_reminders ADD COLUMN {name} {declaration}")
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_companion_reminders_owner_due "
                "ON companion_reminders(owner_id, status, due_at)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_companion_reminders_dispatch "
                "ON companion_reminders(status, due_at, next_attempt_at, lease_until)"
            )

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=10)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=10000")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _check(self, owner_id: str, action: str) -> None:
        if not isinstance(owner_id, str) or not owner_id or not self.authorize(owner_id, action):
            raise PermissionError("Reminder access denied.")

    @staticmethod
    def _serialize(row) -> dict:
        return {key: row[key] for key in (
            "reminder_id", "text", "due_at", "status", "created_at"
        )}

    @staticmethod
    def _utc(value: datetime) -> str:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp_must_be_timezone_aware")
        return value.astimezone(timezone.utc).isoformat()

    def create(self, *, owner_id: str, text: str, due_at: datetime) -> dict:
        self._check(owner_id, "create")
        if not isinstance(text, str) or not text.strip() or len(text.strip()) > self.MAX_TEXT:
            raise ValueError("invalid_reminder_text")
        due = self._utc(due_at)
        now = datetime.now(timezone.utc).isoformat()
        reminder_id = uuid4().hex
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute(
                "SELECT COUNT(*) FROM companion_reminders WHERE owner_id=? AND status='pending'",
                (owner_id,),
            ).fetchone()[0]
            if count >= self.MAX_PER_OWNER:
                raise ValueError("reminder_limit_reached")
            db.execute(
                "INSERT INTO companion_reminders "
                "(reminder_id,owner_id,text,due_at,status,created_at) VALUES (?,?,?,?,?,?)",
                (reminder_id, owner_id, text.strip(), due, "pending", now),
            )
            row = db.execute(
                "SELECT * FROM companion_reminders WHERE reminder_id=?", (reminder_id,)
            ).fetchone()
            return self._serialize(row)

    def list(self, *, owner_id: str, include_inactive: bool = False, limit: int = 100) -> list[dict]:
        self._check(owner_id, "read")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 200:
            raise ValueError("invalid_reminder_limit")
        sql = "SELECT * FROM companion_reminders WHERE owner_id=?"
        params: list[object] = [owner_id]
        if not include_inactive:
            sql += " AND status='pending'"
        sql += " ORDER BY due_at, reminder_id LIMIT ?"
        params.append(limit)
        with self._lock, self._connect() as db:
            return [self._serialize(row) for row in db.execute(sql, params).fetchall()]

    def cancel(self, *, owner_id: str, reminder_id: str) -> bool:
        self._check(owner_id, "cancel")
        if not isinstance(reminder_id, str) or len(reminder_id) != 32:
            raise ValueError("invalid_reminder_id")
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                "UPDATE companion_reminders SET status='cancelled', lease_token=NULL, lease_until=NULL "
                "WHERE reminder_id=? AND owner_id=? AND status='pending'",
                (reminder_id, owner_id),
            )
            return cursor.rowcount == 1

    def claim_due(self, *, now: datetime | None = None, limit: int = 25,
                  lease_seconds: int = 60) -> list[dict]:
        """Lease due reminders for an internal dispatcher; never expose via HTTP."""
        instant = self._utc(now or datetime.now(timezone.utc))
        parsed_now = datetime.fromisoformat(instant)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("invalid_dispatch_limit")
        if not isinstance(lease_seconds, int) or isinstance(lease_seconds, bool) or not 5 <= lease_seconds <= 900:
            raise ValueError("invalid_lease_duration")
        lease_until = (parsed_now + timedelta(seconds=lease_seconds)).isoformat()
        claimed = []
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            # A worker may crash during its final leased attempt. Once that
            # lease expires, make the exhausted state visible instead of leaving
            # an indefinitely pending reminder that can never be claimed again.
            db.execute(
                "UPDATE companion_reminders SET exhausted_at=? "
                "WHERE status='pending' AND attempts>=? AND lease_until IS NOT NULL "
                "AND lease_until<=? AND exhausted_at IS NULL",
                (instant, self.MAX_DELIVERY_ATTEMPTS, instant),
            )
            rows = db.execute(
                "SELECT reminder_id FROM companion_reminders "
                "WHERE status='pending' AND due_at<=? AND attempts<? "
                "AND (next_attempt_at IS NULL OR next_attempt_at<=?) "
                "AND (lease_until IS NULL OR lease_until<=?) AND exhausted_at IS NULL "
                "ORDER BY due_at, reminder_id LIMIT ?",
                (instant, self.MAX_DELIVERY_ATTEMPTS, instant, instant, limit),
            ).fetchall()
            for row in rows:
                token = uuid4().hex
                updated = db.execute(
                    "UPDATE companion_reminders SET lease_token=?, lease_until=?, attempts=attempts+1 "
                    "WHERE reminder_id=? AND status='pending' "
                    "AND (lease_until IS NULL OR lease_until<=?)",
                    (token, lease_until, row["reminder_id"], instant),
                )
                if updated.rowcount != 1:
                    continue
                item = db.execute(
                    "SELECT reminder_id, owner_id, text, due_at, attempts, lease_token, lease_until "
                    "FROM companion_reminders WHERE reminder_id=?", (row["reminder_id"],)
                ).fetchone()
                claimed.append(dict(item))
        return claimed

    def is_lease_current(self, *, reminder_id: str, lease_token: str,
                         now: datetime | None = None) -> bool:
        """Return whether a pending reminder is still owned by this live lease."""
        instant = self._utc(now or datetime.now(timezone.utc))
        if not isinstance(reminder_id, str) or len(reminder_id) != 32:
            raise ValueError("invalid_reminder_id")
        if not isinstance(lease_token, str) or len(lease_token) != 32:
            raise ValueError("invalid_lease_token")
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT 1 FROM companion_reminders WHERE reminder_id=? AND status='pending' "
                "AND lease_token=? AND lease_until>?",
                (reminder_id, lease_token, instant),
            ).fetchone()
            return row is not None

    def acknowledge_delivery(self, *, reminder_id: str, lease_token: str,
                             delivered_at: datetime | None = None) -> bool:
        """Acknowledge a successful send; stale or duplicate acknowledgements fail."""
        stamp = self._utc(delivered_at or datetime.now(timezone.utc))
        if not isinstance(reminder_id, str) or len(reminder_id) != 32:
            raise ValueError("invalid_reminder_id")
        if not isinstance(lease_token, str) or len(lease_token) != 32:
            raise ValueError("invalid_lease_token")
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                "UPDATE companion_reminders SET status='delivered', delivered_at=?, "
                "lease_token=NULL, lease_until=NULL, next_attempt_at=NULL "
                "WHERE reminder_id=? AND status='pending' AND lease_token=? AND lease_until>?",
                (stamp, reminder_id, lease_token, stamp),
            )
            return cursor.rowcount == 1

    def record_delivery_failure(self, *, reminder_id: str, lease_token: str,
                                failed_at: datetime | None = None) -> dict | None:
        """Release a lease and schedule bounded exponential retry."""
        stamp = self._utc(failed_at or datetime.now(timezone.utc))
        instant = datetime.fromisoformat(stamp)
        if not isinstance(reminder_id, str) or len(reminder_id) != 32:
            raise ValueError("invalid_reminder_id")
        if not isinstance(lease_token, str) or len(lease_token) != 32:
            raise ValueError("invalid_lease_token")
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT attempts FROM companion_reminders WHERE reminder_id=? "
                "AND status='pending' AND lease_token=? AND lease_until>?",
                (reminder_id, lease_token, stamp),
            ).fetchone()
            if row is None:
                return None
            # Attempts are counted when a lease is issued, not only when a
            # delivery callback reports failure. This also bounds crash loops.
            attempts = row["attempts"]
            exhausted = attempts >= self.MAX_DELIVERY_ATTEMPTS
            delay = min(2 ** attempts * 5, self.MAX_RETRY_DELAY_SECONDS)
            next_attempt = None if exhausted else (instant + timedelta(seconds=delay)).isoformat()
            db.execute(
                "UPDATE companion_reminders SET attempts=?, next_attempt_at=?, exhausted_at=?, "
                "lease_token=NULL, lease_until=NULL WHERE reminder_id=? AND lease_token=?",
                (attempts, next_attempt, stamp if exhausted else None, reminder_id, lease_token),
            )
            return {"attempts": attempts, "next_attempt_at": next_attempt, "exhausted": exhausted}
