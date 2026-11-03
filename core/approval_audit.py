"""Durable metadata-only approval audit log with hash-chain verification."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
import sqlite3
from typing import Any

from core.sqlite_security import prepare_sqlite_path, secure_sqlite_file

_ALLOWED_EVENTS = frozenset({"requested", "consumed", "approved", "denied", "executed", "execution_failed", "reconciliation_completed", "reconciliation_failed", "reconciliation_uncertain"})
_ZERO_HASH = "0" * 64


class SQLiteApprovalAuditLog:
    """Append-only API; bearer pending IDs and actor identifiers are hashed at rest."""

    def __init__(self, database_path: str, *, identity_key: str | bytes | None = None):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path must be a non-empty path.")
        if identity_key is None:
            identity_key = os.environ.get("YUKI_AUDIT_HMAC_KEY")
        if identity_key is None and os.environ.get("YUKI_ENV", "").lower() == "production":
            raise ValueError(
                "YUKI_AUDIT_HMAC_KEY must be configured in production."
            )
        if isinstance(identity_key, str):
            identity_key = identity_key.encode("utf-8")
        if identity_key is not None and (
            not isinstance(identity_key, bytes) or len(identity_key) < 32
        ):
            raise ValueError("identity_key must contain at least 32 bytes.")
        self._identity_key = identity_key
        self.database_path = prepare_sqlite_path(database_path)
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS approval_audit_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    pending_id TEXT NOT NULL DEFAULT '[redacted]',
                    actor_hash TEXT NOT NULL,
                    skill_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL,
                    pending_id_hash TEXT,
                    session_hash TEXT,
                    event_key TEXT
                )
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(approval_audit_events)")}
            needs_pending_hash = "pending_id_hash" not in columns
            needs_session_hash = "session_hash" not in columns
            needs_event_key = "event_key" not in columns
            if needs_pending_hash or needs_session_hash or needs_event_key:
                # Perform all legacy-column additions and the chain rebuild together.
                # The old append-only triggers must be temporarily removed for migration.
                db.execute("DROP TRIGGER IF EXISTS approval_audit_no_update")
                db.execute("DROP TRIGGER IF EXISTS approval_audit_no_delete")
                db.execute("BEGIN IMMEDIATE")
                try:
                    if needs_pending_hash:
                        db.execute("ALTER TABLE approval_audit_events ADD COLUMN pending_id_hash TEXT")
                    if needs_session_hash:
                        db.execute("ALTER TABLE approval_audit_events ADD COLUMN session_hash TEXT")
                    if needs_pending_hash:
                        rows = db.execute(
                            "SELECT event_id, pending_id FROM approval_audit_events ORDER BY event_id ASC"
                        ).fetchall()
                        for event_id, old_id in rows:
                            db.execute(
                                "UPDATE approval_audit_events SET pending_id_hash = ?, pending_id = '[redacted]' WHERE event_id = ?",
                                (self._digest(old_id), event_id),
                            )
                    if needs_session_hash:
                        db.execute(
                            "UPDATE approval_audit_events SET session_hash = ? WHERE session_hash IS NULL",
                            (self._identity_digest("legacy-session-unspecified"),),
                        )
                    if needs_event_key:
                        db.execute("ALTER TABLE approval_audit_events ADD COLUMN event_key TEXT")
                    self._rebuild_chain(db)
                    db.execute("COMMIT")
                except Exception:
                    db.execute("ROLLBACK")
                    raise
            db.execute("CREATE INDEX IF NOT EXISTS idx_approval_audit_created ON approval_audit_events(created_at, event_id)")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_approval_audit_event_key ON approval_audit_events(event_key) WHERE event_key IS NOT NULL")
            # Defense in depth against accidental SQL mutation. These triggers are
            # not a boundary against an administrator who can alter the DB schema.
            db.execute("""
                CREATE TRIGGER IF NOT EXISTS approval_audit_no_update
                BEFORE UPDATE ON approval_audit_events
                BEGIN
                    SELECT RAISE(ABORT, 'approval audit events are append-only');
                END
            """)
            db.execute("""
                CREATE TRIGGER IF NOT EXISTS approval_audit_no_delete
                BEFORE DELETE ON approval_audit_events
                BEGIN
                    SELECT RAISE(ABORT, 'approval audit events are append-only');
                END
            """)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0, isolation_level=None)
        try:
            db.execute("PRAGMA busy_timeout = 5000")
            yield db
        finally:
            db.close()
            secure_sqlite_file(self.database_path)

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _identity_digest(self, value: str) -> str:
        """Use a deployment secret to prevent offline guessing of actor identities."""
        encoded = value.encode("utf-8")
        if self._identity_key is None:
            # Compatibility mode for local development and pre-key deployments.
            return hashlib.sha256(encoded).hexdigest()
        return hmac.new(self._identity_key, encoded, hashlib.sha256).hexdigest()

    @classmethod
    def _event_hash(cls, event_type, pending_id_hash, actor_hash, session_hash, skill_id, created_at, previous_hash):
        event = {
            "event_type": event_type,
            "pending_id_hash": pending_id_hash,
            "actor_hash": actor_hash,
            "session_hash": session_hash,
            "skill_id": skill_id,
            "created_at": created_at,
            "previous_hash": previous_hash,
        }
        return cls._digest(json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False))

    @classmethod
    def _rebuild_chain(cls, db):
        previous_hash = _ZERO_HASH
        rows = db.execute(
            "SELECT event_id, event_type, pending_id_hash, actor_hash, session_hash, skill_id, created_at "
            "FROM approval_audit_events ORDER BY event_id ASC"
        ).fetchall()
        for event_id, event_type, pending_id_hash, actor_hash, session_hash, skill_id, created_at in rows:
            # If a partially migrated row is encountered, hash its redacted legacy value.
            if not pending_id_hash:
                pending_id_hash = cls._digest("[redacted]")
                db.execute("UPDATE approval_audit_events SET pending_id_hash = ? WHERE event_id = ?",
                           (pending_id_hash, event_id))
            event_hash = cls._event_hash(
                event_type, pending_id_hash, actor_hash, session_hash or cls._digest("legacy-session-unspecified"), skill_id, created_at, previous_hash
            )
            db.execute(
                "UPDATE approval_audit_events SET previous_hash = ?, event_hash = ? WHERE event_id = ?",
                (previous_hash, event_hash, event_id),
            )
            previous_hash = event_hash

    def append(
        self, *, event_type: str, pending_id: str, actor: str, skill_id: str,
        session_id: str = "unspecified", idempotency_key: str | None = None,
    ) -> int:
        if not isinstance(event_type, str) or event_type not in _ALLOWED_EVENTS:
            raise ValueError("Unsupported approval audit event type.")
        for name, value, limit in (("pending_id", pending_id, 128), ("actor", actor, 256), ("session_id", session_id, 256), ("skill_id", skill_id, 120)):
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise ValueError(f"{name} must be a non-empty bounded string.")
        if idempotency_key is not None and (
            not isinstance(idempotency_key, str) or not idempotency_key.strip()
            or len(idempotency_key) > 200
        ):
            raise ValueError("idempotency_key must be a non-empty bounded string.")
        created_at = datetime.now(timezone.utc).isoformat()
        pending_id_hash = self._digest(pending_id)
        actor_hash = self._identity_digest(actor)
        session_hash = self._identity_digest(session_id)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                previous = db.execute(
                    "SELECT event_hash FROM approval_audit_events ORDER BY event_id DESC LIMIT 1"
                ).fetchone()
                previous_hash = previous[0] if previous else _ZERO_HASH
                event_hash = self._event_hash(
                    event_type, pending_id_hash, actor_hash, session_hash, skill_id, created_at, previous_hash
                )
                cursor = db.execute(
                    """INSERT OR IGNORE INTO approval_audit_events
                       (event_type, pending_id, actor_hash, skill_id, created_at,
                        previous_hash, event_hash, pending_id_hash, session_hash, event_key)
                       VALUES (?, '[redacted]', ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (event_type, actor_hash, skill_id, created_at, previous_hash, event_hash,
                     pending_id_hash, session_hash, idempotency_key),
                )
                if cursor.rowcount == 0 and idempotency_key is not None:
                    existing = db.execute(
                        "SELECT event_id, event_type FROM approval_audit_events WHERE event_key = ?",
                        (idempotency_key,),
                    ).fetchone()
                    if existing is None or existing[1] != event_type:
                        raise ValueError("Idempotency key was already used for a different audit event.")
                    event_id = int(existing[0])
                else:
                    event_id = int(cursor.lastrowid)
                db.execute("COMMIT")
                return event_id
            except Exception:
                db.execute("ROLLBACK")
                raise

    def list_events(self, *, limit: int = 1000) -> list[dict[str, Any]]:
        if type(limit) is not int or not 1 <= limit <= 10000:
            raise ValueError("limit must be between 1 and 10000.")
        with self._connect() as db:
            rows = db.execute(
                """SELECT event_id, event_type, pending_id_hash, actor_hash, session_hash, skill_id,
                          created_at, previous_hash, event_hash
                   FROM approval_audit_events ORDER BY event_id DESC LIMIT ?""", (limit,)
            ).fetchall()
        keys = ("event_id", "event_type", "pending_id_hash", "actor_hash", "session_hash", "skill_id",
                "created_at", "previous_hash", "event_hash")
        return [dict(zip(keys, row)) for row in rows]

    def verify_chain(self) -> bool:
        with self._connect() as db:
            rows = db.execute(
                """SELECT event_type, pending_id_hash, actor_hash, session_hash, skill_id, created_at,
                          previous_hash, event_hash
                   FROM approval_audit_events ORDER BY event_id ASC"""
            ).fetchall()
        previous_hash = _ZERO_HASH
        for event_type, pending_id_hash, actor_hash, session_hash, skill_id, created_at, stored_previous, stored_hash in rows:
            if not all(isinstance(value, str) for value in
                       (pending_id_hash, actor_hash, session_hash, skill_id, created_at, stored_previous, stored_hash)):
                return False
            if not hmac.compare_digest(stored_previous, previous_hash):
                return False
            expected = self._event_hash(
                event_type, pending_id_hash, actor_hash, session_hash, skill_id, created_at, stored_previous
            )
            if not hmac.compare_digest(expected, stored_hash):
                return False
            previous_hash = stored_hash
        return True
