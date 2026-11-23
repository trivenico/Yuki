"""Persistent, consent-gated proactive alert deduplication.

This store records bounded operational alert metadata. It does not poll,
schedule work, send notifications, retry tasks, or authorize actions.
"""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import secrets
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path


_IDENTIFIER = re.compile(r"[a-z][a-z0-9_.:-]{0,63}\Z")
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
            columns = {row["name"] for row in db.execute("PRAGMA table_info(proactive_alerts)")}
            for name, declaration in (
                ("status", "TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','acknowledged','resolved'))"),
                ("version", "INTEGER NOT NULL DEFAULT 1"),
                ("acknowledged_at", "TEXT"),
                ("acknowledged_by", "TEXT"),
                ("resolved_at", "TEXT"),
                ("resolved_by", "TEXT"),
            ):
                if name not in columns:
                    db.execute(f"ALTER TABLE proactive_alerts ADD COLUMN {name} {declaration}")
            db.execute("""
                CREATE TABLE IF NOT EXISTS proactive_alert_audit (
                    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    action TEXT NOT NULL CHECK(action IN ('acknowledged','resolved')),
                    actor_id TEXT NOT NULL,
                    previous_version INTEGER NOT NULL,
                    new_version INTEGER NOT NULL,
                    occurred_at TEXT NOT NULL
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_proactive_alert_audit_history
                ON proactive_alert_audit(owner_id, fingerprint, audit_id DESC)
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_proactive_alerts_history
                ON proactive_alerts(owner_id, last_seen DESC)
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS proactive_notification_outbox (
                    notification_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    category TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending','claimed','delivered','failed')),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    lease_token_hash TEXT,
                    lease_expires_at TEXT,
                    delivered_at TEXT,
                    last_error_type TEXT
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_proactive_outbox_pending
                ON proactive_notification_outbox(status, created_at)
            """)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    @staticmethod
    def _validate_text(value, field, maximum):
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            raise ValueError(f"{field} must be non-empty bounded text.")
        if "\x00" in value:
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
            (category + "\0" + subject).encode("utf-8")
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
            if due:
                notification_id = hashlib.sha256(
                    (owner_id + "\0" + fingerprint + "\0" + timestamp + "\0" + str(count)).encode("utf-8")
                ).hexdigest()
                db.execute("""
                    INSERT OR IGNORE INTO proactive_notification_outbox
                        (notification_id, owner_id, fingerprint, category, subject,
                         message, created_at, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
                """, (
                    notification_id, owner_id, fingerprint, category, subject,
                    message, timestamp,
                ))
            else:
                notification_id = None
            db.commit()

        return {
            "notification_id": notification_id,
            "fingerprint": fingerprint,
            "category": category,
            "subject": subject,
            "message": message,
            "first_seen": first_seen,
            "last_seen": timestamp,
            "occurrence_count": count,
            "notification_due": should_notify,
        }


    def claim_notification(
        self, *, owner_id: str, lease_seconds: int = 60,
        max_attempts: int = 5, now: datetime | None = None,
    ) -> dict | None:
        """Atomically lease the oldest pending notification for one owner."""
        owner_id = self._validate_text(owner_id, "owner_id", 200)
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 300:
            raise ValueError("lease_seconds must be between 1 and 300.")
        if type(max_attempts) is not int or not 1 <= max_attempts <= 20:
            raise ValueError("max_attempts must be between 1 and 20.")
        observed_at = self._parse_now(now)
        timestamp = observed_at.isoformat(timespec="seconds")
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""
                UPDATE proactive_notification_outbox
                SET status = 'failed', lease_token_hash = NULL,
                    lease_expires_at = NULL, last_error_type = 'LeaseExpired'
                WHERE owner_id = ? AND status = 'claimed'
                  AND lease_expires_at <= ? AND attempts >= ?
            """, (owner_id, timestamp, max_attempts))
            row = db.execute("""
                SELECT notification_id, category, subject, message, attempts
                FROM proactive_notification_outbox
                WHERE owner_id = ? AND attempts < ? AND (
                    status = 'pending' OR
                    (status = 'claimed' AND lease_expires_at <= ?)
                )
                ORDER BY created_at ASC LIMIT 1
            """, (owner_id, max_attempts, timestamp)).fetchone()
            if row is None:
                db.commit()
                return None
            expires = (observed_at + timedelta(seconds=lease_seconds)).isoformat(timespec="seconds")
            attempts = row["attempts"] + 1
            changed = db.execute("""
                UPDATE proactive_notification_outbox
                SET status = 'claimed', attempts = ?, lease_token_hash = ?,
                    lease_expires_at = ?
                WHERE notification_id = ? AND owner_id = ?
            """, (attempts, token_hash, expires, row["notification_id"], owner_id)).rowcount
            if changed != 1:
                db.rollback()
                return None
            db.commit()
        return {
            "notification_id": row["notification_id"],
            "owner_id": owner_id,
            "category": row["category"],
            "subject": row["subject"],
            "message": row["message"],
            "attempt": attempts,
            "lease_token": token,
        }

    def release_notification(
        self, *, owner_id: str, notification_id: str, lease_token: str,
    ) -> bool:
        """Return a claimed event to pending without consuming an attempt."""
        owner_id = self._validate_text(owner_id, "owner_id", 200)
        notification_id = self._validate_text(notification_id, "notification_id", 64)
        lease_token = self._validate_text(lease_token, "lease_token", 256)
        token_hash = hashlib.sha256(lease_token.encode("utf-8")).hexdigest()
        with closing(self._connect()) as db:
            changed = db.execute("""
                UPDATE proactive_notification_outbox
                SET status = 'pending', lease_token_hash = NULL,
                    lease_expires_at = NULL
                WHERE notification_id = ? AND owner_id = ?
                  AND status = 'claimed' AND lease_token_hash = ?
            """, (notification_id, owner_id, token_hash)).rowcount
            return changed == 1

    def settle_notification(
        self, *, owner_id: str, notification_id: str, lease_token: str,
        delivered: bool, error: BaseException | None = None,
        max_attempts: int = 5, now: datetime | None = None,
    ) -> bool:
        """Settle only the current lease; retryable failures return to pending."""
        owner_id = self._validate_text(owner_id, "owner_id", 200)
        notification_id = self._validate_text(notification_id, "notification_id", 64)
        lease_token = self._validate_text(lease_token, "lease_token", 256)
        if type(delivered) is not bool:
            raise ValueError("delivered must be boolean.")
        if type(max_attempts) is not int or not 1 <= max_attempts <= 20:
            raise ValueError("max_attempts must be between 1 and 20.")
        if error is not None and delivered:
            raise ValueError("a delivered notification cannot include an error.")
        observed_at = self._parse_now(now)
        token_hash = hashlib.sha256(lease_token.encode("utf-8")).hexdigest()
        error_type = None
        if error is not None:
            error_type = re.sub(r"[^A-Za-z0-9_.:-]", "", type(error).__name__)[:64] or "Error"
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""
                SELECT attempts FROM proactive_notification_outbox
                WHERE notification_id = ? AND owner_id = ?
                  AND status = 'claimed' AND lease_token_hash = ?
                  AND lease_expires_at > ?
            """, (
                notification_id, owner_id, token_hash,
                observed_at.isoformat(timespec="seconds"),
            )).fetchone()
            if row is None:
                db.rollback()
                return False
            if delivered:
                db.execute("""
                    UPDATE proactive_notification_outbox
                    SET status = 'delivered', delivered_at = ?,
                        lease_token_hash = NULL, lease_expires_at = NULL,
                        last_error_type = NULL
                    WHERE notification_id = ? AND owner_id = ?
                """, (observed_at.isoformat(timespec="seconds"), notification_id, owner_id))
            else:
                next_status = "pending" if row["attempts"] < max_attempts else "failed"
                db.execute("""
                    UPDATE proactive_notification_outbox
                    SET status = ?, lease_token_hash = NULL,
                        lease_expires_at = NULL, last_error_type = ?
                    WHERE notification_id = ? AND owner_id = ?
                """, (next_status, error_type or "DeliveryFailed", notification_id, owner_id))
            db.commit()
        return True

    def notification_history(self, *, owner_id: str, limit: int = 50) -> list[dict]:
        owner_id = self._validate_text(owner_id, "owner_id", 200)
        if type(limit) is not int or not 1 <= limit <= _MAX_HISTORY:
            raise ValueError("limit must be between 1 and 500.")
        with closing(self._connect()) as db:
            rows = db.execute("""
                SELECT notification_id, category, subject, created_at, status,
                       attempts, delivered_at, last_error_type
                FROM proactive_notification_outbox
                WHERE owner_id = ? ORDER BY created_at DESC LIMIT ?
            """, (owner_id, limit)).fetchall()
        return [dict(row) for row in rows]

    def transition(
        self, *, owner_id: str, alert_id: str, actor_id: str,
        expected_version: int, target_status: str,
        now: datetime | None = None,
    ) -> dict:
        """Acknowledge or resolve one alert with owner and version fencing."""
        owner_id = self._validate_text(owner_id, "owner_id", 200)
        actor_id = self._validate_text(actor_id, "actor_id", 200)
        alert_id = self._validate_text(alert_id, "alert_id", 64)
        if not re.fullmatch(r"[a-f0-9]{64}", alert_id):
            raise ValueError("alert_id must be a SHA-256 fingerprint.")
        if type(expected_version) is not int or expected_version < 1:
            raise ValueError("expected_version must be a positive integer.")
        if target_status not in {"acknowledged", "resolved"}:
            raise ValueError("target_status must be acknowledged or resolved.")
        timestamp = self._parse_now(now).isoformat(timespec="seconds")
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""
                SELECT status, version FROM proactive_alerts
                WHERE owner_id = ? AND fingerprint = ?
            """, (owner_id, alert_id)).fetchone()
            if row is None:
                db.rollback()
                raise KeyError("alert_not_found")
            if row["version"] != expected_version:
                db.rollback()
                raise ValueError("alert_version_conflict")
            allowed = {"acknowledged": {"active"}, "resolved": {"active", "acknowledged"}}
            if row["status"] not in allowed[target_status]:
                db.rollback()
                raise ValueError("invalid_alert_transition")
            new_version = expected_version + 1
            if target_status == "acknowledged":
                db.execute("""
                    UPDATE proactive_alerts SET status = ?, version = ?,
                        acknowledged_at = ?, acknowledged_by = ?
                    WHERE owner_id = ? AND fingerprint = ? AND version = ?
                """, (target_status, new_version, timestamp, actor_id, owner_id, alert_id, expected_version))
            else:
                db.execute("""
                    UPDATE proactive_alerts SET status = ?, version = ?,
                        resolved_at = ?, resolved_by = ?
                    WHERE owner_id = ? AND fingerprint = ? AND version = ?
                """, (target_status, new_version, timestamp, actor_id, owner_id, alert_id, expected_version))
            db.execute("""
                INSERT INTO proactive_alert_audit
                    (owner_id, fingerprint, action, actor_id, previous_version, new_version, occurred_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (owner_id, alert_id, target_status, actor_id, expected_version, new_version, timestamp))
            db.commit()
        return {"alert_id": alert_id, "status": target_status, "version": new_version,
                "actor_id": actor_id, "occurred_at": timestamp}

    def acknowledge(self, *, owner_id: str, alert_id: str, actor_id: str,
                    expected_version: int, now: datetime | None = None) -> dict:
        return self.transition(owner_id=owner_id, alert_id=alert_id, actor_id=actor_id,
                               expected_version=expected_version, target_status="acknowledged", now=now)

    def resolve(self, *, owner_id: str, alert_id: str, actor_id: str,
                expected_version: int, now: datetime | None = None) -> dict:
        return self.transition(owner_id=owner_id, alert_id=alert_id, actor_id=actor_id,
                               expected_version=expected_version, target_status="resolved", now=now)

    def audit_history(self, *, owner_id: str, alert_id: str, limit: int = 50) -> list[dict]:
        owner_id = self._validate_text(owner_id, "owner_id", 200)
        alert_id = self._validate_text(alert_id, "alert_id", 64)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100.")
        with closing(self._connect()) as db:
            rows = db.execute("""
                SELECT action, actor_id, previous_version, new_version, occurred_at
                FROM proactive_alert_audit WHERE owner_id = ? AND fingerprint = ?
                ORDER BY audit_id DESC LIMIT ?
            """, (owner_id, alert_id, limit)).fetchall()
        return [dict(row) for row in rows]

    def recent(self, *, owner_id: str, limit: int = 50) -> list[dict]:
        owner_id = self._validate_text(owner_id, "owner_id", 200)
        if type(limit) is not int or not 1 <= limit <= _MAX_HISTORY:
            raise ValueError("limit must be between 1 and 500.")
        with closing(self._connect()) as db:
            rows = db.execute("""
                SELECT fingerprint AS alert_id, category, subject, message,
                       first_seen, last_seen, last_notified, occurrence_count,
                       status, version, acknowledged_at, acknowledged_by,
                       resolved_at, resolved_by
                FROM proactive_alerts WHERE owner_id = ?
                ORDER BY last_seen DESC LIMIT ?
            """, (owner_id, limit)).fetchall()
        return [dict(row) for row in rows]
