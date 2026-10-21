"""Durable idempotency ledger for approval-gated side effects.

The ledger records intent before execution and prevents a reused idempotency key
from silently starting a second execution. It cannot make an external side effect
exactly-once; callers must reconcile running records after crashes before retrying.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

_ALLOWED_TRANSITIONS = {
    "prepared": {"running", "failed", "uncertain"},
    "running": {"completed", "failed", "uncertain"},
    "completed": set(),
    "failed": set(),
    "uncertain": set(),
}


@dataclass(frozen=True)
class ExecutionRecord:
    idempotency_key: str
    action_hash: str
    state: str
    result_json: str | None
    created_at: str
    updated_at: str


class SQLiteExecutionLedger:
    """SQLite-backed action execution intent and idempotency ledger."""

    def __init__(self, database_path: str, *, timeout_seconds: float = 5.0) -> None:
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path must be a non-empty path.")
        if type(timeout_seconds) not in (int, float) or not 0.1 <= timeout_seconds <= 60:
            raise ValueError("timeout_seconds must be between 0.1 and 60.")
        self.database_path = str(Path(database_path).expanduser())
        self.timeout_seconds = float(timeout_seconds)
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS action_execution_ledger (
                    idempotency_key TEXT PRIMARY KEY,
                    action_hash TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state IN
                        ('prepared', 'running', 'completed', 'failed', 'uncertain')),
                    result_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_execution_ledger_state "
                "ON action_execution_ledger(state, updated_at)"
            )

    def _connect(self):
        db = sqlite3.connect(
            self.database_path, timeout=self.timeout_seconds, isolation_level=None
        )
        db.execute(f"PRAGMA busy_timeout = {int(self.timeout_seconds * 1000)}")
        return db

    @staticmethod
    def action_digest(action: dict[str, Any]) -> str:
        if not isinstance(action, dict):
            raise ValueError("action must be a JSON object.")
        try:
            encoded = json.dumps(
                action, ensure_ascii=False, allow_nan=False, sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("action must be JSON-safe.") from exc
        if len(encoded.encode("utf-8")) > 64 * 1024:
            raise ValueError("action exceeds 64 KiB.")
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def begin(self, idempotency_key: str, action: dict[str, Any]) -> tuple[ExecutionRecord, bool]:
        """Persist intent before side effects; return (record, created).

        Reusing a key for a different action raises ValueError. Reusing a key for
        the same action returns the existing record and never resets its state.
        """
        if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 200:
            raise ValueError("idempotency_key is required and must be at most 200 characters.")
        digest = self.action_digest(action)
        now = datetime.now(timezone.utc).isoformat()
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT idempotency_key, action_hash, state, result_json, created_at, updated_at "
                "FROM action_execution_ledger WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if row is not None:
                if row[1] != digest:
                    raise ValueError("idempotency_key is already bound to a different action.")
                db.execute("COMMIT")
                return self._record(row), False
            db.execute(
                "INSERT INTO action_execution_ledger "
                "(idempotency_key, action_hash, state, result_json, created_at, updated_at) "
                "VALUES (?, ?, 'prepared', NULL, ?, ?)",
                (idempotency_key, digest, now, now),
            )
            db.execute("COMMIT")
            return ExecutionRecord(idempotency_key, digest, "prepared", None, now, now), True
        except Exception:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    @staticmethod
    def _record(row) -> ExecutionRecord:
        return ExecutionRecord(*row)

    def get(self, idempotency_key: str) -> ExecutionRecord | None:
        if not isinstance(idempotency_key, str) or not idempotency_key:
            return None
        db = self._connect()
        try:
            row = db.execute(
                "SELECT idempotency_key, action_hash, state, result_json, created_at, updated_at "
                "FROM action_execution_ledger WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            return self._record(row) if row is not None else None
        finally:
            db.close()

    def transition(self, idempotency_key: str, new_state: str, *, result: Any = None) -> ExecutionRecord:
        if new_state not in _ALLOWED_TRANSITIONS:
            raise ValueError("Unsupported execution state.")
        try:
            result_json = None if result is None else json.dumps(
                result, ensure_ascii=False, allow_nan=False, sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("result must be JSON-safe.") from exc
        if result_json is not None and len(result_json.encode("utf-8")) > 64 * 1024:
            raise ValueError("result exceeds 64 KiB.")
        now = datetime.now(timezone.utc).isoformat()
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT idempotency_key, action_hash, state, result_json, created_at, updated_at "
                "FROM action_execution_ledger WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if row is None:
                raise KeyError("Execution idempotency key not found.")
            if new_state not in _ALLOWED_TRANSITIONS[row[2]]:
                raise ValueError(f"Invalid execution transition: {row[2]} -> {new_state}.")
            db.execute(
                "UPDATE action_execution_ledger SET state = ?, result_json = ?, updated_at = ? "
                "WHERE idempotency_key = ?",
                (new_state, result_json, now, idempotency_key),
            )
            updated = db.execute(
                "SELECT idempotency_key, action_hash, state, result_json, created_at, updated_at "
                "FROM action_execution_ledger WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            db.execute("COMMIT")
            return self._record(updated)
        except Exception:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def list_reconciliation_required(self, *, limit: int = 100) -> list[ExecutionRecord]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000.")
        db = self._connect()
        try:
            rows = db.execute(
                "SELECT idempotency_key, action_hash, state, result_json, created_at, updated_at "
                "FROM action_execution_ledger WHERE state IN ('prepared', 'running', 'uncertain') "
                "ORDER BY updated_at, idempotency_key LIMIT ?",
                (limit,),
            ).fetchall()
            return [self._record(row) for row in rows]
        finally:
            db.close()
