"""SQLite persistence for pending approval snapshots.

Pending actions are short-lived operational state. Creator actor/session bindings are
hashed at rest and checked again at approval time; callers must provide trusted identity.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from typing import Any

from core.sqlite_security import prepare_sqlite_path, secure_sqlite_file


@dataclass(frozen=True)
class StoredPendingAction:
    pending_id: str
    skill_id: str
    arguments: dict[str, Any]
    action: dict[str, Any]
    expires_at: str
    actor_hash: str
    session_hash: str
    status: str = "pending"


class SQLitePendingApprovalStore:
    """Bounded, restart-persistent pending approvals with atomic one-time claims."""

    def __init__(
        self,
        database_path: str,
        *,
        ttl_seconds: int = 300,
        max_pending: int = 128,
        identity_key: str | bytes | None = None,
    ):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path must be a non-empty path.")
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 3600:
            raise ValueError("ttl_seconds must be between 1 and 3600.")
        if type(max_pending) is not int or not 1 <= max_pending <= 4096:
            raise ValueError("max_pending must be between 1 and 4096.")
        if identity_key is None:
            identity_key = os.environ.get("YUKI_AUDIT_HMAC_KEY")
        if isinstance(identity_key, str):
            identity_key = identity_key.encode("utf-8")
        if identity_key is not None and (
            not isinstance(identity_key, bytes) or len(identity_key) < 32
        ):
            raise ValueError("identity_key must contain at least 32 bytes.")
        if identity_key is None and os.environ.get("YUKI_ENV", "").lower() == "production":
            raise ValueError("YUKI_AUDIT_HMAC_KEY must be configured in production.")
        self._identity_key = identity_key
        self.database_path = prepare_sqlite_path(database_path)
        self.ttl_seconds = ttl_seconds
        self.max_pending = max_pending
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS pending_approvals (
                pending_id TEXT PRIMARY KEY,
                skill_id TEXT NOT NULL,
                arguments_json TEXT NOT NULL,
                action_json TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                actor_hash TEXT,
                session_hash TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                updated_at TEXT,
                reconciliation_decision TEXT,
                reconciliation_actor_hash TEXT,
                reconciliation_session_hash TEXT
            )""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(pending_approvals)")}
            if "actor_hash" not in columns:
                db.execute("ALTER TABLE pending_approvals ADD COLUMN actor_hash TEXT")
            if "session_hash" not in columns:
                db.execute("ALTER TABLE pending_approvals ADD COLUMN session_hash TEXT")
            if "status" not in columns:
                db.execute("ALTER TABLE pending_approvals ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'")
            if "updated_at" not in columns:
                db.execute("ALTER TABLE pending_approvals ADD COLUMN updated_at TEXT")
            if "reconciliation_decision" not in columns:
                db.execute("ALTER TABLE pending_approvals ADD COLUMN reconciliation_decision TEXT")
            if "reconciliation_actor_hash" not in columns:
                db.execute("ALTER TABLE pending_approvals ADD COLUMN reconciliation_actor_hash TEXT")
            if "reconciliation_session_hash" not in columns:
                db.execute("ALTER TABLE pending_approvals ADD COLUMN reconciliation_session_hash TEXT")
            db.execute("CREATE INDEX IF NOT EXISTS idx_pending_approval_expiry ON pending_approvals(expires_at)")

    @contextmanager
    def _connect(self):
        """Yield a SQLite connection and always close it (important on Windows)."""
        db = sqlite3.connect(self.database_path, timeout=5.0, isolation_level=None)
        try:
            db.execute("PRAGMA busy_timeout = 5000")
            yield db
        finally:
            db.close()
            secure_sqlite_file(self.database_path)

    def _identity_hash(self, value: str) -> str:
        encoded = value.encode("utf-8")
        if self._identity_key is None:
            return hashlib.sha256(encoded).hexdigest()
        return hmac.new(self._identity_key, encoded, hashlib.sha256).hexdigest()

    @staticmethod
    def _encode(value: dict[str, Any]) -> str:
        if not isinstance(value, dict):
            raise ValueError("Pending action snapshots must be objects.")
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > 16 * 1024:
            raise ValueError("Pending action exceeds 16 KiB.")
        return encoded

    def put(self, skill_id: str, arguments: dict[str, Any], action: dict[str, Any], *,
            actor: str, session_id: str) -> StoredPendingAction:
        if not isinstance(skill_id, str) or not skill_id.strip() or len(skill_id) > 120:
            raise ValueError("skill_id is required and must be bounded.")
        if not isinstance(actor, str) or not actor.strip() or not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("Trusted actor and session identity are required.")
        args_json, action_json = self._encode(arguments), self._encode(action)
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(seconds=self.ttl_seconds)).isoformat()
        actor_hash, session_hash = self._identity_hash(actor), self._identity_hash(session_id)
        item = StoredPendingAction(secrets.token_urlsafe(24), skill_id, json.loads(args_json),
                                   json.loads(action_json), expires, actor_hash, session_hash)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                retention_cutoff = (now - timedelta(days=7)).isoformat()
                db.execute(
                    """DELETE FROM pending_approvals
                       WHERE (status = 'pending' AND expires_at <= ?)
                          OR (status IN ('denied', 'completed', 'execution_failed')
                              AND updated_at <= ?)""",
                    (now.isoformat(), retention_cutoff),
                )
                count = db.execute(
                    """SELECT COUNT(*) FROM pending_approvals
                       WHERE status IN ('pending', 'claimed', 'approved', 'executing', 'recovery_required')"""
                ).fetchone()[0]
                if count >= self.max_pending:
                    raise RuntimeError("Pending approval capacity reached.")
                db.execute("""INSERT INTO pending_approvals
                    (pending_id, skill_id, arguments_json, action_json, expires_at, actor_hash, session_hash, status, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
                    (item.pending_id, skill_id, args_json, action_json, expires, actor_hash, session_hash, now.isoformat()))
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise
        return item

    def get(self, pending_id: str) -> StoredPendingAction | None:
        if not isinstance(pending_id, str) or not pending_id:
            return None
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            row = db.execute("""SELECT skill_id, arguments_json, action_json, expires_at, actor_hash, session_hash, status
                FROM pending_approvals WHERE pending_id = ?""", (pending_id,)).fetchone()
            if row is None:
                return None
            if row[3] <= now and row[6] == "pending":
                db.execute("DELETE FROM pending_approvals WHERE pending_id = ?", (pending_id,))
                return None
            # Old rows from before identity binding are rejected, not silently upgraded.
            if not row[4] or not row[5]:
                db.execute("DELETE FROM pending_approvals WHERE pending_id = ?", (pending_id,))
                return None
            return StoredPendingAction(pending_id, row[0], json.loads(row[1]), json.loads(row[2]),
                                       row[3], row[4], row[5], row[6])

    def claim(self, pending_id: str, *, actor: str, session_id: str) -> StoredPendingAction | None:
        """Atomically claim a receipt without deleting its recovery snapshot."""
        if not isinstance(pending_id, str) or not pending_id:
            return None
        if not isinstance(actor, str) or not actor.strip() or not isinstance(session_id, str) or not session_id.strip():
            return None
        now = datetime.now(timezone.utc).isoformat()
        actor_hash, session_hash = self._identity_hash(actor), self._identity_hash(session_id)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("""SELECT skill_id, arguments_json, action_json, expires_at,
                    actor_hash, session_hash, status FROM pending_approvals WHERE pending_id = ?""",
                    (pending_id,)).fetchone()
                if row is None or row[3] <= now or row[4] != actor_hash or row[5] != session_hash or row[6] != "pending":
                    db.execute("COMMIT")
                    return None
                cursor = db.execute("""UPDATE pending_approvals SET status = 'claimed', updated_at = ?
                    WHERE pending_id = ? AND status = 'pending'""", (now, pending_id))
                if cursor.rowcount != 1:
                    db.execute("ROLLBACK")
                    return None
                db.execute("COMMIT")
                return StoredPendingAction(pending_id, row[0], json.loads(row[1]), json.loads(row[2]),
                                           row[3], row[4], row[5], "claimed")
            except Exception:
                db.execute("ROLLBACK")
                raise

    def transition(self, pending_id: str, *, expected: str, new_status: str) -> bool:
        reconciled = {"reconciled_completed", "reconciled_failed", "reconciled_uncertain"}
        allowed = {
            "pending": {"denied"},
            "claimed": {"approved", "denied", "recovery_required"} | reconciled,
            "approved": {"executing", "recovery_required"} | reconciled,
            "executing": {"completed", "execution_failed", "recovery_required"} | reconciled,
            "recovery_required": reconciled,
        }
        if new_status not in allowed.get(expected, set()):
            raise ValueError("Invalid approval lifecycle transition.")
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            cursor = db.execute("""UPDATE pending_approvals SET status = ?, updated_at = ?
                WHERE pending_id = ? AND status = ?""", (new_status, now, pending_id, expected))
            return cursor.rowcount == 1

    def begin_reconciliation(
        self, pending_id: str, *, decision: str, actor: str, session_id: str
    ) -> bool:
        """Atomically reserve one recovery decision; retries must match its identity."""
        if decision not in {"completed", "failed", "uncertain"}:
            raise ValueError("Invalid reconciliation decision.")
        if not all(isinstance(value, str) and value.strip() for value in (actor, session_id)):
            raise ValueError("Reconciliation actor and session are required.")
        actor_hash = self._identity_hash(actor)
        session_hash = self._identity_hash(session_id)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status, reconciliation_decision, reconciliation_actor_hash, "
                "reconciliation_session_hash FROM pending_approvals WHERE pending_id = ?",
                (pending_id,),
            ).fetchone()
            if row is None:
                db.execute("ROLLBACK")
                return False
            status, stored_decision, stored_actor, stored_session = row
            if status == "recovery_required":
                cursor = db.execute(
                    """UPDATE pending_approvals
                       SET status = 'reconciling', reconciliation_decision = ?,
                           reconciliation_actor_hash = ?, reconciliation_session_hash = ?,
                           updated_at = ?
                       WHERE pending_id = ? AND status = 'recovery_required'""",
                    (decision, actor_hash, session_hash, datetime.now(timezone.utc).isoformat(), pending_id),
                )
                db.execute("COMMIT")
                return cursor.rowcount == 1
            same_retry = (
                status == "reconciling" and stored_decision == decision
                and hmac.compare_digest(stored_actor or "", actor_hash)
                and hmac.compare_digest(stored_session or "", session_hash)
            )
            db.execute("COMMIT")
            return same_retry

    def finish_reconciliation(self, pending_id: str, *, decision: str) -> bool:
        """Finalize a reserved decision without allowing a competing operator to win."""
        terminal = {
            "completed": "reconciled_completed",
            "failed": "reconciled_failed",
            "uncertain": "recovery_required",
        }
        if decision not in terminal:
            raise ValueError("Invalid reconciliation decision.")
        with self._connect() as db:
            cursor = db.execute(
                """UPDATE pending_approvals
                   SET status = ?, updated_at = ?,
                       reconciliation_decision = NULL,
                       reconciliation_actor_hash = NULL,
                       reconciliation_session_hash = NULL
                   WHERE pending_id = ? AND status = 'reconciling'
                     AND reconciliation_decision = ?""",
                (terminal[decision], datetime.now(timezone.utc).isoformat(), pending_id, decision),
            )
            return cursor.rowcount == 1

    def list_recovery_required(self, *, limit: int = 100) -> list[dict[str, str]]:
        """Return ambiguous/interrupted actions for explicit operator reconciliation."""
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000.")
        with self._connect() as db:
            rows = db.execute(
                """SELECT pending_id, skill_id, status, updated_at
                   FROM pending_approvals
                   WHERE status IN ('claimed', 'approved', 'executing', 'recovery_required', 'reconciling')
                   ORDER BY updated_at ASC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [
            {"pending_id": row[0], "skill_id": row[1], "status": row[2], "updated_at": row[3] or ""}
            for row in rows
        ]

    def find_recovery_by_reference(self, reference: str) -> dict[str, str] | None:
        """Resolve a non-bearer 12-character suffix only when it is unambiguous."""
        if not isinstance(reference, str) or len(reference) != 12 or not all(ch.isalnum() or ch in "-_" for ch in reference):
            return None
        with self._connect() as db:
            rows = db.execute(
                """SELECT pending_id, skill_id, status, updated_at
                   FROM pending_approvals
                   WHERE substr(pending_id, -12) = ?
                     AND status IN ('claimed', 'approved', 'executing', 'recovery_required', 'reconciling')
                   LIMIT 2""",
                (reference,),
            ).fetchall()
        if len(rows) != 1:
            return None
        row = rows[0]
        return {
            "pending_id": row[0],
            "skill_id": row[1],
            "status": row[2],
            "updated_at": row[3] or "",
        }

    def get_status(self, pending_id: str) -> str | None:
        with self._connect() as db:
            row = db.execute("SELECT status FROM pending_approvals WHERE pending_id = ?", (pending_id,)).fetchone()
            return row[0] if row else None

    def consume(self, pending_id: str, *, actor: str, session_id: str) -> StoredPendingAction | None:
        """Backward-compatible alias for claim; never deletes a recovery snapshot."""
        return self.claim(pending_id, actor=actor, session_id=session_id)

    def deny(self, pending_id: str) -> bool:
        """Mark a still-pending request denied without deleting its audit snapshot."""
        if not isinstance(pending_id, str) or not pending_id:
            return False
        return self.transition(
            pending_id, expected="pending", new_status="denied"
        )

    def discard_pending(self, pending_id: str) -> bool:
        """Remove only a not-yet-claimed request that failed initial audit creation."""
        if not isinstance(pending_id, str) or not pending_id:
            return False
        with self._connect() as db:
            cursor = db.execute(
                "DELETE FROM pending_approvals WHERE pending_id = ? AND status = 'pending'",
                (pending_id,),
            )
            return cursor.rowcount == 1
