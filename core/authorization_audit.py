"""Durable, metadata-minimizing audit sink for central authorization decisions.

Events are append-only through this API, serialized across processes by SQLite
BEGIN IMMEDIATE, and hash chained in sequence order. The chain is tamper-evident
against accidental or partial modification; a privileged attacker able to rewrite
the database can recompute it, so production deployments should periodically
anchor the head hash in an independently controlled system.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import math
import hashlib
import hmac
import json
import sqlite3
from typing import Protocol, runtime_checkable

from core.authorization import AuthorizationDecision, AuthorizationRequest
from core.sqlite_file_security import prepare_private_sqlite_path

_ZERO_HASH = "0" * 64


@dataclass(frozen=True)
class AuthorizationAuditCheckpoint:
    """Immutable checkpoint receipt returned by an independent anchor service."""

    event_id: int
    event_hash: str
    published_at: str = ""


@runtime_checkable
class AuthorizationAuditAnchor(Protocol):
    """Independent append-only checkpoint service; implementation is host supplied."""

    def publish(self, checkpoint: AuthorizationAuditCheckpoint) -> None: ...
    def latest(self) -> AuthorizationAuditCheckpoint | None: ...


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

    def checkpoint(self) -> AuthorizationAuditCheckpoint:
        """Capture a consistent local head for publication to an independent system."""
        with self._connect() as db:
            row = db.execute(
                "SELECT event_id, event_hash FROM authorization_audit_events ORDER BY event_id DESC LIMIT 1"
            ).fetchone()
        return AuthorizationAuditCheckpoint(int(row[0]), row[1], datetime.now(timezone.utc).isoformat()) if row else AuthorizationAuditCheckpoint(0, _ZERO_HASH, datetime.now(timezone.utc).isoformat())

    def publish_checkpoint(self, anchor: AuthorizationAuditAnchor) -> AuthorizationAuditCheckpoint:
        """Publish the current chain head; anchor must be independently controlled."""
        if not isinstance(anchor, AuthorizationAuditAnchor):
            raise TypeError("independent_authorization_audit_anchor_required")
        checkpoint = self.checkpoint()
        anchor.publish(checkpoint)
        return checkpoint

    def verify_anchor(
        self,
        anchor: AuthorizationAuditAnchor,
        *,
        max_age_seconds: float | None = None,
        now: datetime | None = None,
    ) -> bool:
        """Verify the anchored prefix and, optionally, checkpoint publication freshness."""
        if not isinstance(anchor, AuthorizationAuditAnchor):
            raise TypeError("independent_authorization_audit_anchor_required")
        try:
            checkpoint = anchor.latest()
        except Exception:
            return False
        if checkpoint is None:
            return False
        if (
            not isinstance(checkpoint, AuthorizationAuditCheckpoint)
            or type(checkpoint.event_id) is not int
            or checkpoint.event_id < 0
            or not isinstance(checkpoint.event_hash, str)
            or len(checkpoint.event_hash) != 64
            or any(char not in "0123456789abcdef" for char in checkpoint.event_hash)
            or not isinstance(checkpoint.published_at, str)
            or not checkpoint.published_at
            or len(checkpoint.published_at) > 64
        ):
            return False
        if max_age_seconds is not None:
            if isinstance(max_age_seconds, bool) or not isinstance(max_age_seconds, (int, float)):
                return False
            if not math.isfinite(max_age_seconds) or max_age_seconds <= 0:
                return False
            try:
                published_at = datetime.fromisoformat(checkpoint.published_at)
                current_time = now or datetime.now(timezone.utc)
                if published_at.tzinfo is None or current_time.tzinfo is None:
                    return False
                age_seconds = (current_time - published_at).total_seconds()
            except (TypeError, ValueError, OverflowError):
                return False
            if age_seconds < -30 or age_seconds > max_age_seconds:
                return False
        if not self.verify_chain():
            return False
        if checkpoint.event_id == 0:
            return hmac.compare_digest(checkpoint.event_hash, _ZERO_HASH)
        with self._connect() as db:
            row = db.execute(
                "SELECT event_hash FROM authorization_audit_events WHERE event_id = ?",
                (checkpoint.event_id,),
            ).fetchone()
        return bool(row) and hmac.compare_digest(row[0], checkpoint.event_hash)


class AuthorizationAuditCheckpointPublisher:
    """One-shot publisher intended for an externally managed scheduler.

    This deliberately does not create threads, timers, or background tasks. The
    host scheduler invokes run_once() at its configured interval. Missing or
    inconsistent anchors are never silently initialized or overwritten.
    """

    def __init__(self, audit_log: SQLiteAuthorizationAuditLog, anchor: AuthorizationAuditAnchor):
        if not isinstance(audit_log, SQLiteAuthorizationAuditLog):
            raise TypeError("durable_authorization_audit_log_required")
        if not isinstance(anchor, AuthorizationAuditAnchor):
            raise TypeError("independent_authorization_audit_anchor_required")
        self.audit_log = audit_log
        self.anchor = anchor

    def run_once(self) -> AuthorizationAuditCheckpoint:
        """Verify the existing anchor, then publish a newer local head if needed."""
        try:
            latest = self.anchor.latest()
        except Exception as exc:
            raise RuntimeError("authorization_audit_anchor_unavailable") from exc
        if latest is None:
            raise RuntimeError("authorization_audit_anchor_requires_operator_bootstrap")
        if not self.audit_log.verify_anchor(self.anchor):
            raise RuntimeError("authorization_audit_anchor_integrity_mismatch")
        current = self.audit_log.checkpoint()
        if current.event_id < latest.event_id:
            raise RuntimeError("authorization_audit_local_history_behind_anchor")
        if current.event_id == latest.event_id:
            if not hmac.compare_digest(current.event_hash, latest.event_hash):
                raise RuntimeError("authorization_audit_anchor_head_mismatch")
        self.anchor.publish(current)
        published = self.anchor.latest()
        if published != current:
            raise RuntimeError("authorization_audit_anchor_publication_unconfirmed")
        return current
