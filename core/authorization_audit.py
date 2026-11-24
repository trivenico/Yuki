"""Durable, metadata-minimizing audit sink for central authorization decisions.

Events are append-only through this API, serialized across processes by SQLite
BEGIN IMMEDIATE, and hash chained in sequence order. The chain is tamper-evident
against accidental or partial modification; a privileged attacker able to rewrite
the database can recompute it, so production deployments should periodically
anchor the head hash in an independently controlled system.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import json
import sqlite3

from core.authorization import AuthorizationDecision, AuthorizationRequest
from core.sqlite_file_security import prepare_private_sqlite_path

_ZERO_HASH = "0" * 64


class SQLiteAuthorizationAuditLog:
    """Cross-process durable authorization audit writer for production gateways."""

    def __init__(self, database_path: str):
        self.database_path = prepare_private_sqlite_path(database_path)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS authorization_audit_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                occurred_at TEXT NOT NULL,
                principal_hash TEXT NOT NULL,
                tenant_hash TEXT,
                agent_hash TEXT,
                action TEXT NOT NULL,
                resource_hash TEXT NOT NULL,
                task_hash TEXT,
                delegation_hash TEXT,
                approval_hash TEXT,
                risk TEXT NOT NULL,
                allowed INTEGER NOT NULL CHECK (allowed IN (0,1)),
                decision_hash TEXT NOT NULL,
                policy_revision_hash TEXT NOT NULL,
                reason_code TEXT NOT NULL,
                previous_hash TEXT NOT NULL,
                event_hash TEXT NOT NULL
            )""")
            db.execute("""CREATE INDEX IF NOT EXISTS authorization_audit_time_idx
                ON authorization_audit_events(event_id, occurred_at)""")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=10.0, isolation_level=None)
        try:
            db.execute("PRAGMA busy_timeout=10000")
            yield db
        finally:
            db.close()

    @staticmethod
    def _digest(value: str | None) -> str | None:
        if value is None:
            return None
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _hash_event(event: dict) -> str:
        encoded = json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def __call__(self, request: AuthorizationRequest, decision: AuthorizationDecision) -> int:
        if not isinstance(request, AuthorizationRequest) or not isinstance(decision, AuthorizationDecision):
            raise TypeError("typed_authorization_audit_event_required")
        principal = request.principal
        occurred_at = datetime.now(timezone.utc).isoformat()
        values = {
            "occurred_at": occurred_at,
            "principal_hash": self._digest(principal.principal_id),
            "tenant_hash": self._digest(principal.tenant_id),
            "agent_hash": self._digest(principal.agent_id),
            "action": request.action,
            "resource_hash": self._digest(request.resource),
            "task_hash": self._digest(request.task_id),
            "delegation_hash": self._digest(request.delegation_id),
            "approval_hash": self._digest(request.approval_id),
            "risk": request.risk,
            "allowed": int(decision.allowed),
            "decision_hash": self._digest(decision.decision_id),
            "policy_revision_hash": self._digest(decision.policy_revision),
            "reason_code": decision.reason_code,
        }
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                previous = db.execute(
                    "SELECT event_hash FROM authorization_audit_events ORDER BY event_id DESC LIMIT 1"
                ).fetchone()
                previous_hash = previous[0] if previous else _ZERO_HASH
                event = dict(values, previous_hash=previous_hash)
                event_hash = self._hash_event(event)
                columns = tuple(values) + ("previous_hash", "event_hash")
                placeholders = ",".join("?" for _ in columns)
                cursor = db.execute(
                    "INSERT INTO authorization_audit_events (" + ",".join(columns) +
                    ") VALUES (" + placeholders + ")",
                    tuple(values[name] for name in values) + (previous_hash, event_hash),
                )
                db.execute("COMMIT")
                return int(cursor.lastrowid)
            except Exception:
                db.execute("ROLLBACK")
                raise

    def verify_chain(self) -> bool:
        columns = (
            "occurred_at", "principal_hash", "tenant_hash", "agent_hash", "action",
            "resource_hash", "task_hash", "delegation_hash", "approval_hash", "risk",
            "allowed", "decision_hash", "policy_revision_hash", "reason_code",
        )
        with self._connect() as db:
            rows = db.execute(
                "SELECT " + ",".join(columns) +
                ",previous_hash,event_hash FROM authorization_audit_events ORDER BY event_id ASC"
            ).fetchall()
        previous_hash = _ZERO_HASH
        for row in rows:
            values = dict(zip(columns, row[:len(columns)]))
            stored_previous, stored_hash = row[-2:]
            if not hmac.compare_digest(stored_previous, previous_hash):
                return False
            if self._hash_event(dict(values, previous_hash=stored_previous)) != stored_hash:
                return False
            previous_hash = stored_hash
        return True

    def head_hash(self) -> str:
        with self._connect() as db:
            row = db.execute(
                "SELECT event_hash FROM authorization_audit_events ORDER BY event_id DESC LIMIT 1"
            ).fetchone()
        return row[0] if row else _ZERO_HASH
