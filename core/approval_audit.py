"""Durable metadata-only approval audit log with hash-chain verification."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
from typing import Any

_ALLOWED_EVENTS = frozenset({"requested", "approved", "denied", "executed", "execution_failed"})
_ZERO_HASH = "0" * 64


class SQLiteApprovalAuditLog:
    """Append-only API; bearer pending IDs and actor identifiers are hashed at rest."""

    def __init__(self, database_path: str):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path must be a non-empty path.")
        self.database_path = str(Path(database_path).expanduser())
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
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
                    pending_id_hash TEXT
                )
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(approval_audit_events)")}
            if "pending_id_hash" not in columns:
                # Migrate legacy records before allowing new events to be appended.
                db.execute("BEGIN IMMEDIATE")
                try:
                    db.execute("ALTER TABLE approval_audit_events ADD COLUMN pending_id_hash TEXT")
                    rows = db.execute(
                        "SELECT event_id, pending_id FROM approval_audit_events ORDER BY event_id ASC"
                    ).fetchall()
                    for event_id, old_id in rows:
                        db.execute(
                            "UPDATE approval_audit_events SET pending_id_hash = ?, pending_id = '[redacted]' WHERE event_id = ?",
                            (self._digest(old_id), event_id),
                        )
                    self._rebuild_chain(db)
                    db.execute("COMMIT")
                except Exception:
                    db.execute("ROLLBACK")
                    raise
            db.execute("CREATE INDEX IF NOT EXISTS idx_approval_audit_created ON approval_audit_events(created_at, event_id)")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0, isolation_level=None)
        try:
            db.execute("PRAGMA busy_timeout = 5000")
            yield db
        finally:
            db.close()

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @classmethod
    def _event_hash(cls, event_type, pending_id_hash, actor_hash, skill_id, created_at, previous_hash):
        event = {
            "event_type": event_type,
            "pending_id_hash": pending_id_hash,
            "actor_hash": actor_hash,
            "skill_id": skill_id,
            "created_at": created_at,
            "previous_hash": previous_hash,
        }
        return cls._digest(json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False))

    @classmethod
    def _rebuild_chain(cls, db):
        previous_hash = _ZERO_HASH
        rows = db.execute(
            "SELECT event_id, event_type, pending_id_hash, actor_hash, skill_id, created_at "
            "FROM approval_audit_events ORDER BY event_id ASC"
        ).fetchall()
        for event_id, event_type, pending_id_hash, actor_hash, skill_id, created_at in rows:
            # If a partially migrated row is encountered, hash its redacted legacy value.
            if not pending_id_hash:
                pending_id_hash = cls._digest("[redacted]")
                db.execute("UPDATE approval_audit_events SET pending_id_hash = ? WHERE event_id = ?",
                           (pending_id_hash, event_id))
            event_hash = cls._event_hash(
                event_type, pending_id_hash, actor_hash, skill_id, created_at, previous_hash
            )
            db.execute(
                "UPDATE approval_audit_events SET previous_hash = ?, event_hash = ? WHERE event_id = ?",
                (previous_hash, event_hash, event_id),
            )
            previous_hash = event_hash

    def append(self, *, event_type: str, pending_id: str, actor: str, skill_id: str) -> int:
        if not isinstance(event_type, str) or event_type not in _ALLOWED_EVENTS:
            raise ValueError("Unsupported approval audit event type.")
        for name, value, limit in (("pending_id", pending_id, 128), ("actor", actor, 256), ("skill_id", skill_id, 120)):
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise ValueError(f"{name} must be a non-empty bounded string.")
        created_at = datetime.now(timezone.utc).isoformat()
        pending_id_hash = self._digest(pending_id)
        actor_hash = self._digest(actor)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                previous = db.execute(
                    "SELECT event_hash FROM approval_audit_events ORDER BY event_id DESC LIMIT 1"
                ).fetchone()
                previous_hash = previous[0] if previous else _ZERO_HASH
                event_hash = self._event_hash(
                    event_type, pending_id_hash, actor_hash, skill_id, created_at, previous_hash
                )
                cursor = db.execute(
                    """INSERT INTO approval_audit_events
                       (event_type, pending_id, actor_hash, skill_id, created_at,
                        previous_hash, event_hash, pending_id_hash)
                       VALUES (?, '[redacted]', ?, ?, ?, ?, ?, ?)""",
                    (event_type, actor_hash, skill_id, created_at, previous_hash, event_hash, pending_id_hash),
                )
                db.execute("COMMIT")
                return int(cursor.lastrowid)
            except Exception:
                db.execute("ROLLBACK")
                raise

    def list_events(self, *, limit: int = 1000) -> list[dict[str, Any]]:
        if type(limit) is not int or not 1 <= limit <= 10000:
            raise ValueError("limit must be between 1 and 10000.")
        with self._connect() as db:
            rows = db.execute(
                """SELECT event_id, event_type, pending_id_hash, actor_hash, skill_id,
                          created_at, previous_hash, event_hash
                   FROM approval_audit_events ORDER BY event_id DESC LIMIT ?""", (limit,)
            ).fetchall()
        keys = ("event_id", "event_type", "pending_id_hash", "actor_hash", "skill_id",
                "created_at", "previous_hash", "event_hash")
        return [dict(zip(keys, row)) for row in rows]

    def find_incomplete_executions(self, *, limit: int = 1000) -> list[dict[str, Any]]:
        """Return approved actions without a terminal receipt for manual reconciliation.

        This is deliberately diagnostic only: it never replays an action. Callers must
        reconcile external side effects before marking an item resolved. Identifiers
        are represented by their stored hashes, not bearer tokens or raw actor IDs.
        """
        if type(limit) is not int or not 1 <= limit <= 10000:
            raise ValueError("limit must be between 1 and 10000.")
        with self._connect() as db:
            rows = db.execute(
                """SELECT event_id, event_type, pending_id_hash, actor_hash, skill_id, created_at
                   FROM approval_audit_events ORDER BY event_id ASC"""
            ).fetchall()

        states: dict[str, dict[str, Any]] = {}
        for event_id, event_type, pending_hash, actor_hash, skill_id, created_at in rows:
            if not isinstance(pending_hash, str) or not pending_hash:
                continue
            if event_type == "approved":
                states[pending_hash] = {
                    "pending_id_hash": pending_hash,
                    "actor_hash": actor_hash,
                    "skill_id": skill_id,
                    "approved_event_id": event_id,
                    "approved_at": created_at,
                }
            elif event_type in {"executed", "execution_failed", "denied"}:
                states.pop(pending_hash, None)

        incomplete = sorted(states.values(), key=lambda item: item["approved_event_id"])
        return incomplete[-limit:]

    def verify_chain(self) -> bool:
        with self._connect() as db:
            rows = db.execute(
                """SELECT event_type, pending_id_hash, actor_hash, skill_id, created_at,
                          previous_hash, event_hash
                   FROM approval_audit_events ORDER BY event_id ASC"""
            ).fetchall()
        previous_hash = _ZERO_HASH
        for event_type, pending_id_hash, actor_hash, skill_id, created_at, stored_previous, stored_hash in rows:
            if not all(isinstance(value, str) for value in
                       (pending_id_hash, actor_hash, skill_id, created_at, stored_previous, stored_hash)):
                return False
            if not hmac.compare_digest(stored_previous, previous_hash):
                return False
            expected = self._event_hash(
                event_type, pending_id_hash, actor_hash, skill_id, created_at, stored_previous
            )
            if not hmac.compare_digest(expected, stored_hash):
                return False
            previous_hash = stored_hash
        return True
