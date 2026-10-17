"""Redacted, append-only SQLite audit trail for approval decisions.

Never store prompts, raw arguments, tokens, or exception text. Actor/session values
must be supplied by a trusted identity layer. This is an operational audit log,
not a tamper-proof or externally replicated compliance ledger.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any


class SQLiteApprovalAuditLog:
    def __init__(self, database_path: str, *, max_event_bytes: int = 4096):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path must be a non-empty path.")
        if type(max_event_bytes) is not int or not 512 <= max_event_bytes <= 16384:
            raise ValueError("max_event_bytes must be between 512 and 16384.")
        self.database_path = str(Path(database_path).expanduser())
        self.max_event_bytes = max_event_bytes
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS approval_audit_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                occurred_at TEXT NOT NULL,
                event_type TEXT NOT NULL,
                pending_id_hash TEXT,
                skill_id TEXT NOT NULL,
                action_hash TEXT NOT NULL,
                actor_hash TEXT,
                session_hash TEXT,
                outcome TEXT NOT NULL,
                details_json TEXT NOT NULL
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_approval_audit_time ON approval_audit_events(occurred_at)")

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0, isolation_level=None)
        db.execute("PRAGMA busy_timeout = 5000")
        return db

    @staticmethod
    def _hash(value: str | None) -> str | None:
        if value is None:
            return None
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _action_hash(action: dict[str, Any]) -> str:
        canonical = json.dumps(action, ensure_ascii=False, allow_nan=False,
                               sort_keys=True, separators=(",", ":"))
        if len(canonical.encode("utf-8")) > 16 * 1024:
            raise ValueError("Audit action exceeds 16 KiB.")
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def record(self, *, event_type: str, skill_id: str, action: dict[str, Any],
               outcome: str, actor: str | None = None, session_id: str | None = None,
               pending_id: str | None = None, details: dict[str, Any] | None = None) -> int:
        allowed_types = {"requested", "approved", "denied", "expired", "executed", "failed"}
        allowed_outcomes = {"pending", "approved", "denied", "success", "failure", "expired"}
        if event_type not in allowed_types or outcome not in allowed_outcomes:
            raise ValueError("Unsupported audit event type or outcome.")
        if not isinstance(skill_id, str) or not skill_id.strip() or len(skill_id) > 120:
            raise ValueError("skill_id must be non-empty bounded text.")
        if details is not None and not isinstance(details, dict):
            raise ValueError("details must be an object.")
        # Details are deliberately allowlisted scalar metadata; arbitrary strings
        # could accidentally carry prompts or sensitive arguments.
        safe_details: dict[str, str | int | float | bool | None] = {}
        allowed_detail_keys = {"status", "steps", "stage", "risk", "attempt", "duration_ms", "retry_count", "reason_code"}
        for key, value in (details or {}).items():
            if (not isinstance(key, str) or key not in allowed_detail_keys or len(key) > 64
                    or type(value) not in (str, int, float, bool, type(None))
                    or (isinstance(value, str) and len(value) > 128)
                    or (isinstance(value, float) and (value != value or abs(value) == float("inf")))):
                raise ValueError("Audit details must be bounded scalar metadata.")
            safe_details[key] = value
        details_json = json.dumps(safe_details, ensure_ascii=True, allow_nan=False, sort_keys=True)
        if len(details_json.encode("utf-8")) > self.max_event_bytes:
            raise ValueError("Audit details exceed the event size limit.")
        occurred_at = datetime.now(timezone.utc).isoformat()
        action_hash = self._action_hash(action)
        with self._connect() as db:
            cursor = db.execute(
                """INSERT INTO approval_audit_events
                (occurred_at, event_type, pending_id_hash, skill_id, action_hash,
                 actor_hash, session_hash, outcome, details_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (occurred_at, event_type, self._hash(pending_id), skill_id, action_hash,
                 self._hash(actor), self._hash(session_id), outcome, details_json),
            )
            return int(cursor.lastrowid)

    def list_events(self, *, limit: int = 100) -> list[dict[str, Any]]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000.")
        with self._connect() as db:
            rows = db.execute(
                """SELECT event_id, occurred_at, event_type, pending_id_hash, skill_id,
                   action_hash, actor_hash, session_hash, outcome, details_json
                   FROM approval_audit_events ORDER BY event_id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [{
            "event_id": row[0], "occurred_at": row[1], "event_type": row[2],
            "pending_id_hash": row[3], "skill_id": row[4], "action_hash": row[5],
            "actor_hash": row[6], "session_hash": row[7], "outcome": row[8],
            "details": json.loads(row[9]),
        } for row in rows]
