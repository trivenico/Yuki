"""Durable, principal-scoped pending proposals for confirmation-required tools.

A proposal stores the exact canonical tool arguments and trusted action identity.
Approval only changes proposal state; execution remains a separate guarded step
that must consume an action-bound approval token.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import secrets
import sqlite3
from typing import Any, Mapping

from core.sqlite_file_security import prepare_private_sqlite_path
from core.tool_action_context import ToolActionContext


class PendingToolActionProposalError(RuntimeError):
    """Raised when durable proposal state cannot be safely accessed."""


@dataclass(frozen=True)
class PendingToolActionProposal:
    proposal_id: str
    principal_id: str
    action_id: str
    idempotency_key: str
    tool_name: str
    request_digest: str
    arguments: dict[str, Any]
    status: str
    created_at: str
    expires_at: str
    decided_at: str | None


class SQLitePendingToolActionProposalStore:
    """SQLite state machine for exact, expiring tool-action proposals."""

    _STATUSES = {"pending", "approved", "rejected", "expired", "cancelled"}

    def __init__(self, database_path: str, *, ttl_seconds: int = 300):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path must be a non-empty path.")
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 3600:
            raise ValueError("ttl_seconds must be between 1 and 3600.")
        self.database_path = prepare_private_sqlite_path(database_path)
        self.ttl_seconds = ttl_seconds
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS pending_tool_action_proposals (
                proposal_id TEXT PRIMARY KEY,
                principal_id TEXT NOT NULL,
                action_id TEXT NOT NULL UNIQUE,
                idempotency_key TEXT NOT NULL UNIQUE,
                tool_name TEXT NOT NULL,
                request_digest TEXT NOT NULL,
                arguments_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN
                    ('pending', 'approved', 'rejected', 'expired', 'cancelled')),
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                decided_at TEXT
            )""")
            db.execute("""CREATE INDEX IF NOT EXISTS idx_pending_tool_proposals_owner_status
                ON pending_tool_action_proposals(principal_id, status, created_at)""")
            db.execute("""CREATE INDEX IF NOT EXISTS idx_pending_tool_proposals_expiry
                ON pending_tool_action_proposals(status, expires_at)""")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0, isolation_level=None)
        try:
            db.execute("PRAGMA busy_timeout = 5000")
            yield db
        finally:
            db.close()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _row_to_proposal(row) -> PendingToolActionProposal:
        return PendingToolActionProposal(
            proposal_id=row[0], principal_id=row[1], action_id=row[2],
            idempotency_key=row[3], tool_name=row[4], request_digest=row[5],
            arguments=json.loads(row[6]), status=row[7], created_at=row[8],
            expires_at=row[9], decided_at=row[10],
        )

    @staticmethod
    def _select_columns() -> str:
        return ("proposal_id, principal_id, action_id, idempotency_key, tool_name, "
                "request_digest, arguments_json, status, created_at, expires_at, decided_at")

    def create(self, context: ToolActionContext, arguments: Mapping[str, Any], *,
               principal_id: str) -> PendingToolActionProposal:
        """Persist the immutable proposal before returning it for user review."""
        if not isinstance(context, ToolActionContext):
            raise TypeError("context must be a trusted ToolActionContext.")
        context.verify(principal_id, context.tool_name, arguments)
        try:
            arguments_json = json.dumps(
                dict(arguments), ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), allow_nan=False,
            )
        except (TypeError, ValueError, UnicodeEncodeError) as exc:
            raise ValueError("proposal arguments must be finite JSON data.") from exc
        if len(arguments_json.encode("utf-8")) > 32 * 1024:
            raise ValueError("proposal arguments exceed the size limit.")
        now = self._now()
        proposal_id = secrets.token_urlsafe(24)
        created_at = now.isoformat()
        expires_at = (now + timedelta(seconds=self.ttl_seconds)).isoformat()
        with self._connect() as db:
            try:
                db.execute("""INSERT INTO pending_tool_action_proposals
                    (proposal_id, principal_id, action_id, idempotency_key, tool_name,
                     request_digest, arguments_json, status, created_at, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
                    (proposal_id, principal_id, context.action_id,
                     context.idempotency_key, context.tool_name,
                     context.request_digest, arguments_json, created_at, expires_at))
            except sqlite3.IntegrityError as exc:
                raise PendingToolActionProposalError(
                    "action context already has a persisted proposal."
                ) from exc
            row = db.execute(
                f"SELECT {self._select_columns()} FROM pending_tool_action_proposals "
                "WHERE proposal_id = ?", (proposal_id,),
            ).fetchone()
        return self._row_to_proposal(row)

    def get(self, proposal_id: str, *, principal_id: str) -> PendingToolActionProposal | None:
        """Read a proposal only for its authenticated owner."""
        if not isinstance(proposal_id, str) or not proposal_id or len(proposal_id) > 128:
            return None
        if not isinstance(principal_id, str) or not principal_id:
            raise ValueError("authenticated principal is required.")
        now = self._now().isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("""UPDATE pending_tool_action_proposals
                    SET status = 'expired', decided_at = ?
                    WHERE proposal_id = ? AND principal_id = ?
                      AND status = 'pending' AND expires_at <= ?""",
                    (now, proposal_id, principal_id, now))
                row = db.execute(
                    f"SELECT {self._select_columns()} FROM pending_tool_action_proposals "
                    "WHERE proposal_id = ? AND principal_id = ?",
                    (proposal_id, principal_id),
                ).fetchone()
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise
        return self._row_to_proposal(row) if row is not None else None

    def list_pending(self, *, principal_id: str, limit: int = 50) -> tuple[PendingToolActionProposal, ...]:
        """List unexpired pending proposals belonging to one principal."""
        if not isinstance(principal_id, str) or not principal_id:
            raise ValueError("authenticated principal is required.")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100.")
        now = self._now().isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("""UPDATE pending_tool_action_proposals
                    SET status = 'expired', decided_at = ?
                    WHERE principal_id = ? AND status = 'pending' AND expires_at <= ?""",
                    (now, principal_id, now))
                rows = db.execute(
                    f"SELECT {self._select_columns()} FROM pending_tool_action_proposals "
                    "WHERE principal_id = ? AND status = 'pending' "
                    "ORDER BY created_at LIMIT ?", (principal_id, limit),
                ).fetchall()
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise
        return tuple(self._row_to_proposal(row) for row in rows)

    def decide(self, proposal_id: str, *, principal_id: str,
               decision: str) -> PendingToolActionProposal | None:
        """Atomically approve or reject a still-pending, unexpired proposal."""
        if decision not in {"approved", "rejected"}:
            raise ValueError("decision must be approved or rejected.")
        if not isinstance(principal_id, str) or not principal_id:
            raise ValueError("authenticated principal is required.")
        if not isinstance(proposal_id, str) or not proposal_id or len(proposal_id) > 128:
            return None
        now = self._now().isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("""UPDATE pending_tool_action_proposals
                    SET status = 'expired', decided_at = ?
                    WHERE proposal_id = ? AND principal_id = ?
                      AND status = 'pending' AND expires_at <= ?""",
                    (now, proposal_id, principal_id, now))
                changed = db.execute("""UPDATE pending_tool_action_proposals
                    SET status = ?, decided_at = ?
                    WHERE proposal_id = ? AND principal_id = ?
                      AND status = 'pending' AND expires_at > ?""",
                    (decision, now, proposal_id, principal_id, now)).rowcount
                row = None
                if changed == 1:
                    row = db.execute(
                        f"SELECT {self._select_columns()} FROM pending_tool_action_proposals "
                        "WHERE proposal_id = ? AND principal_id = ?",
                        (proposal_id, principal_id),
                    ).fetchone()
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise
        return self._row_to_proposal(row) if row is not None else None

    def cancel(self, proposal_id: str, *, principal_id: str) -> bool:
        """Cancel only an owner's proposal that has not yet been decided."""
        if not isinstance(principal_id, str) or not principal_id:
            raise ValueError("authenticated principal is required.")
        now = self._now().isoformat()
        with self._connect() as db:
            db.execute("""UPDATE pending_tool_action_proposals
                SET status = 'cancelled', decided_at = ?
                WHERE proposal_id = ? AND principal_id = ? AND status = 'pending'
                  AND expires_at > ?""",
                (now, proposal_id, principal_id, now))
            return db.execute("SELECT changes()").fetchone()[0] == 1
