"""Durable metadata-only approval and MCP execution audit events."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path

from core.sqlite_file_security import prepare_private_sqlite_path
import sqlite3
import uuid
import re
from typing import Any

_ALLOWED_EVENTS = frozenset({
    "requested", "approved", "denied", "executed", "execution_started",
    "execution_failed", "execution_unavailable", "outcome_unknown",
})
_ZERO_HASH = "0" * 64


class SQLiteApprovalAuditLog:
    """Append-only API; bearer IDs and actor identifiers are hashed at rest."""

    def __init__(self, database_path: str):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path must be a non-empty path.")
        self.database_path = prepare_private_sqlite_path(database_path)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS approval_audit_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT NOT NULL,
                pending_id TEXT NOT NULL DEFAULT '[redacted]', actor_hash TEXT NOT NULL,
                skill_id TEXT NOT NULL, created_at TEXT NOT NULL, previous_hash TEXT NOT NULL,
                event_hash TEXT NOT NULL, pending_id_hash TEXT)""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(approval_audit_events)")}
            if "pending_id_hash" not in columns:
                db.execute("BEGIN IMMEDIATE")
                try:
                    db.execute("ALTER TABLE approval_audit_events ADD COLUMN pending_id_hash TEXT")
                    rows = db.execute("SELECT event_id, pending_id FROM approval_audit_events ORDER BY event_id ASC").fetchall()
                    for event_id, old_id in rows:
                        db.execute("UPDATE approval_audit_events SET pending_id_hash=?, pending_id='[redacted]' WHERE event_id=?",
                                   (self._digest(old_id), event_id))
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
            db.execute("PRAGMA busy_timeout=5000")
            yield db
        finally:
            db.close()

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @classmethod
    def _event_hash(cls, event_type, pending_id_hash, actor_hash, skill_id, created_at, previous_hash):
        event = {"event_type": event_type, "pending_id_hash": pending_id_hash,
                 "actor_hash": actor_hash, "skill_id": skill_id, "created_at": created_at,
                 "previous_hash": previous_hash}
        return cls._digest(json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False))

    @classmethod
    def _rebuild_chain(cls, db):
        previous_hash = _ZERO_HASH
        rows = db.execute("SELECT event_id,event_type,pending_id_hash,actor_hash,skill_id,created_at FROM approval_audit_events ORDER BY event_id").fetchall()
        for event_id, event_type, pending_id_hash, actor_hash, skill_id, created_at in rows:
            if not pending_id_hash:
                pending_id_hash = cls._digest("[redacted]")
                db.execute("UPDATE approval_audit_events SET pending_id_hash=? WHERE event_id=?", (pending_id_hash, event_id))
            event_hash = cls._event_hash(event_type, pending_id_hash, actor_hash, skill_id, created_at, previous_hash)
            db.execute("UPDATE approval_audit_events SET previous_hash=?,event_hash=? WHERE event_id=?", (previous_hash, event_hash, event_id))
            previous_hash = event_hash

    def append(self, *, event_type: str, pending_id: str, actor: str, skill_id: str) -> int:
        if not isinstance(event_type, str) or event_type not in _ALLOWED_EVENTS:
            raise ValueError("Unsupported approval audit event type.")
        for name, value, limit in (("pending_id", pending_id, 128), ("actor", actor, 256), ("skill_id", skill_id, 120)):
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise ValueError(f"{name} must be a non-empty bounded string.")
        created_at = datetime.now(timezone.utc).isoformat()
        pending_hash, actor_hash = self._digest(pending_id), self._digest(actor)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                previous = db.execute("SELECT event_hash FROM approval_audit_events ORDER BY event_id DESC LIMIT 1").fetchone()
                previous_hash = previous[0] if previous else _ZERO_HASH
                event_hash = self._event_hash(event_type, pending_hash, actor_hash, skill_id, created_at, previous_hash)
                cursor = db.execute("""INSERT INTO approval_audit_events
                    (event_type,pending_id,actor_hash,skill_id,created_at,previous_hash,event_hash,pending_id_hash)
                    VALUES (?,'[redacted]',?,?,?,?,?,?)""",
                    (event_type, actor_hash, skill_id, created_at, previous_hash, event_hash, pending_hash))
                db.execute("COMMIT")
                return int(cursor.lastrowid)
            except Exception:
                db.execute("ROLLBACK")
                raise

    def list_events(self, *, limit: int = 1000) -> list[dict[str, Any]]:
        if type(limit) is not int or not 1 <= limit <= 10000:
            raise ValueError("limit must be between 1 and 10000.")
        with self._connect() as db:
            rows = db.execute("""SELECT event_id,event_type,pending_id_hash,actor_hash,skill_id,
                created_at,previous_hash,event_hash FROM approval_audit_events ORDER BY event_id DESC LIMIT ?""", (limit,)).fetchall()
        keys = ("event_id","event_type","pending_id_hash","actor_hash","skill_id","created_at","previous_hash","event_hash")
        return [dict(zip(keys, row)) for row in rows]

    def verify_chain(self) -> bool:
        with self._connect() as db:
            rows = db.execute("""SELECT event_type,pending_id_hash,actor_hash,skill_id,created_at,
                previous_hash,event_hash FROM approval_audit_events ORDER BY event_id ASC""").fetchall()
        previous_hash = _ZERO_HASH
        for event_type, pending_hash, actor_hash, skill_id, created_at, stored_previous, stored_hash in rows:
            if not all(isinstance(value, str) for value in (pending_hash, actor_hash, skill_id, created_at, stored_previous, stored_hash)):
                return False
            if not hmac.compare_digest(stored_previous, previous_hash):
                return False
            expected = self._event_hash(event_type, pending_hash, actor_hash, skill_id, created_at, stored_previous)
            if not hmac.compare_digest(expected, stored_hash):
                return False
            previous_hash = stored_hash
        return True


class MCPExecutionAudit:
    """Append-only lifecycle callback with exact per-tool identity."""

    _EVENTS = frozenset({"execution_started", "executed", "execution_failed", "outcome_unknown"})

    def __init__(self, audit_log: SQLiteApprovalAuditLog, *, owner_id: str,
                 provider_id: str, account_id: str):
        for label, value in (("owner_id", owner_id), ("provider_id", provider_id),
                             ("account_id", account_id)):
            if not isinstance(value, str) or not value.strip() or len(value) > 256:
                raise ValueError("invalid_" + label)
        if not isinstance(audit_log, SQLiteApprovalAuditLog):
            raise TypeError("trusted_sqlite_approval_audit_required")
        self._log, self._owner = audit_log, owner_id
        self._provider, self._account = provider_id, account_id

    def __call__(self, event: str, invocation_id: str, tool_name: str) -> int:
        if event not in self._EVENTS:
            raise ValueError("unsupported_mcp_execution_event")
        if not isinstance(invocation_id, str) or not re.fullmatch(r"[a-f0-9]{32}", invocation_id):
            raise ValueError("invalid_mcp_invocation_id")
        if not isinstance(tool_name, str) or not tool_name.strip() or len(tool_name) > 128:
            raise ValueError("invalid_mcp_tool_name")
        identity = json.dumps([self._provider, self._account, tool_name], separators=(",", ":"))
        skill_id = "mcp:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return self._log.append(event_type=event, pending_id=invocation_id,
                                actor=self._owner, skill_id=skill_id)


class MCPToolPolicyAudit:
    """Adapter from MCP policy decisions to the existing metadata-only audit log."""

    _EVENTS = {
        "requested": "requested",
        "owner_approved": "approved",
        "authorized_read_only": "approved",
        "denied": "denied",
    }

    def __init__(self, audit_log: SQLiteApprovalAuditLog):
        if not isinstance(audit_log, SQLiteApprovalAuditLog):
            raise TypeError("trusted_sqlite_approval_audit_required")
        self._log = audit_log

    def __call__(self, event: dict[str, Any]) -> int:
        if not isinstance(event, dict):
            raise ValueError("invalid_mcp_policy_audit_event")
        event_type = self._EVENTS.get(event.get("event"))
        owner, provider, account, tool = (
            event.get("owner_id"), event.get("provider_id"),
            event.get("account_id"), event.get("tool_name"),
        )
        for value in (owner, provider, account, tool):
            if not isinstance(value, str) or not value.strip() or len(value) > 256:
                raise ValueError("invalid_mcp_policy_audit_identity")
        # A policy decision has its own correlation token. The execution client
        # creates a separate invocation token immediately before dispatch.
        return self._log.append(
            event_type=event_type,
            pending_id=uuid.uuid4().hex,
            actor=owner,
            skill_id=":".join((provider, account, tool))[:120],
        ) if event_type else (_ for _ in ()).throw(ValueError("unsupported_mcp_policy_audit_event"))
