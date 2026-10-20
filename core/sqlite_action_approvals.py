"""Durable SQLite-backed one-time approval tokens for skill dispatch.

Tokens are bound to action id, risk, canonical JSON payload digest and expiry.
Consumption is a single DELETE ... RETURNING operation inside a write
transaction, so concurrent consumers cannot both execute the same token.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import secrets
import sqlite3
from pathlib import Path
from typing import Any


def _snapshot(arguments: dict[str, Any]) -> tuple[str, dict[str, Any], str]:
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be an object")
    try:
        encoded = json.dumps(arguments, ensure_ascii=False, allow_nan=False,
                             sort_keys=True, separators=(",", ":"))
        detached = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise ValueError("arguments must be JSON-safe") from exc
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return encoded, detached, digest


@dataclass(frozen=True)
class SQLiteActionApproval:
    approval_id: str
    skill_id: str
    risk: str
    arguments: dict[str, Any]
    payload_sha256: str
    created_at: float
    expires_at: float


class SQLiteActionApprovalStore:
    """Drop-in approval store implementing the ActionApprovalStore API."""

    def __init__(self, database_path: str, *, ttl_seconds: int = 300,
                 max_pending: int = 128, clock=None):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path must be a non-empty path")
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 86400:
            raise ValueError("ttl_seconds must be from 1 to 86400")
        if type(max_pending) is not int or not 1 <= max_pending <= 4096:
            raise ValueError("max_pending must be from 1 to 4096")
        self.database_path = str(Path(database_path).expanduser())
        self.ttl_seconds = ttl_seconds
        self.max_pending = max_pending
        self._clock = clock
        db_path = Path(self.database_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # Reject symlinks before SQLite opens the path; checking only after
        # connect would already have followed an attacker-controlled target.
        if db_path.is_symlink():
            raise ValueError("Approval database must not be a symbolic link.")
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS action_approvals (
                    approval_id TEXT PRIMARY KEY,
                    skill_id TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    arguments_json TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_action_approval_expiry ON action_approvals(expires_at)")
        # Approval tokens are bearer credentials: restrict the database file
        # to the current user wherever POSIX permission bits are supported.
        # Do not follow a symlink to an unexpected target.
        if os.name == "posix":
            os.chmod(db_path, 0o600)

    def _now(self) -> float:
        if self._clock is not None:
            return float(self._clock())
        from time import time
        return time()

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0, isolation_level=None)
        db.execute("PRAGMA busy_timeout = 5000")
        return db

    @staticmethod
    def _record(row) -> SQLiteActionApproval:
        return SQLiteActionApproval(row[0], row[1], row[2], json.loads(row[3]),
                                    row[4], row[5], row[6])

    def create(self, skill_id: str, arguments: dict[str, Any], *, risk: str = "high"):
        if not isinstance(skill_id, str) or not skill_id.strip() or len(skill_id) > 120:
            raise ValueError("A bounded skill id is required")
        if risk not in {"low", "medium", "high", "critical"}:
            raise ValueError("A valid risk level is required")
        encoded, detached, digest = _snapshot(arguments)
        if len(encoded.encode("utf-8")) > 16 * 1024:
            raise ValueError("Approval payload exceeds 16 KiB")
        now = self._now()
        approval_id = secrets.token_urlsafe(32)
        expires = now + self.ttl_seconds
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("DELETE FROM action_approvals WHERE expires_at <= ?", (now,))
                count = db.execute("SELECT COUNT(*) FROM action_approvals").fetchone()[0]
                if count >= self.max_pending:
                    raise RuntimeError("Pending approval capacity reached")
                db.execute("""INSERT INTO action_approvals
                    (approval_id, skill_id, risk, arguments_json, payload_sha256, created_at, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (approval_id, skill_id, risk, encoded, digest, now, expires))
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise
        return SQLiteActionApproval(approval_id, skill_id, risk, detached, digest, now, expires)

    def consume(self, approval_id: str, *, skill_id: str,
                arguments: dict[str, Any], risk: str):
        if not isinstance(approval_id, str) or not approval_id:
            return None
        try:
            _, detached, digest = _snapshot(arguments)
        except ValueError:
            return None
        now = self._now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("""DELETE FROM action_approvals
                    WHERE approval_id = ? AND skill_id = ? AND risk = ?
                    AND payload_sha256 = ? AND expires_at > ?
                    RETURNING approval_id, skill_id, risk, arguments_json,
                              payload_sha256, created_at, expires_at""",
                    (approval_id, skill_id, risk, digest, now)).fetchone()
                # A token with the wrong action/payload/risk is burned, preventing retries.
                db.execute("DELETE FROM action_approvals WHERE approval_id = ?", (approval_id,))
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise
        if row is None:
            return None
        item = self._record(row)
        # Defend against unexpected persisted payload inconsistency.
        if item.arguments != detached:
            return None
        return item

    def deny(self, approval_id: str) -> bool:
        if not isinstance(approval_id, str) or not approval_id:
            return False
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute("DELETE FROM action_approvals WHERE approval_id = ?", (approval_id,))
            return cursor.rowcount == 1
