"""Durable, idempotent journal for sandboxed proposal-check executions.

The journal records lifecycle intent and outcomes; it does not itself provide
exactly-once process execution. Recovery marks interrupted work for explicit
reconciliation rather than silently replaying potentially side-effectful work.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Iterable


class ExecutionJournalError(RuntimeError):
    """An execution journal operation violated the lifecycle contract."""


TERMINAL_STATES = frozenset({"succeeded", "failed", "timed_out", "cancelled", "cleanup_required", "cleaned"})
ACTIVE_STATES = frozenset({"created", "running"})
_ALLOWED_TRANSITIONS = {
    "created": frozenset({"running", "cancelled"}),
    "running": frozenset({"succeeded", "failed", "timed_out", "cancelled", "cleanup_required"}),
    "cleanup_required": frozenset({"cleaned", "failed"}),
    "succeeded": frozenset(),
    "failed": frozenset(),
    "timed_out": frozenset(),
    "cancelled": frozenset(),
    "cleaned": frozenset(),
}


@dataclass(frozen=True)
class ExecutionRecord:
    execution_id: str
    context_sha256: str
    state: str
    created_at: str
    updated_at: str
    result_json: str | None
    cleanup_confirmed: bool


class SQLiteExecutionJournal:
    """SQLite-backed execution state with idempotency and transition validation."""

    def __init__(self, database_path: str):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path must be a non-empty path")
        self.database_path = str(Path(database_path).expanduser())
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS sandbox_executions (
                    execution_id TEXT PRIMARY KEY,
                    context_sha256 TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    result_json TEXT,
                    cleanup_confirmed INTEGER NOT NULL DEFAULT 0
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_sandbox_execution_state ON sandbox_executions(state, updated_at)")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0, isolation_level=None)
        try:
            db.execute("PRAGMA busy_timeout=5000")
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            yield db
        finally:
            db.close()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def context_digest(context: dict) -> str:
        try:
            canonical = json.dumps(context, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ExecutionJournalError("Execution context must be finite JSON data.") from exc
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _record(row) -> ExecutionRecord:
        return ExecutionRecord(
            execution_id=row[0], context_sha256=row[1], state=row[2],
            created_at=row[3], updated_at=row[4], result_json=row[5],
            cleanup_confirmed=bool(row[6]),
        )

    def create(self, execution_id: str, context: dict) -> ExecutionRecord:
        if not isinstance(execution_id, str) or not execution_id or len(execution_id) > 128 or "\x00" in execution_id:
            raise ExecutionJournalError("execution_id must be a non-empty bounded string")
        digest = self.context_digest(context)
        now = self._now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    "SELECT execution_id, context_sha256, state, created_at, updated_at, result_json, cleanup_confirmed FROM sandbox_executions WHERE execution_id=?",
                    (execution_id,),
                ).fetchone()
                if row:
                    if row[1] != digest:
                        raise ExecutionJournalError("execution_id already exists with different context")
                    db.execute("COMMIT")
                    return self._record(row)
                db.execute(
                    "INSERT INTO sandbox_executions(execution_id, context_sha256, state, created_at, updated_at) VALUES(?,?,?,?,?)",
                    (execution_id, digest, "created", now, now),
                )
                db.execute("COMMIT")
            except Exception:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
        return self.get(execution_id)

    def get(self, execution_id: str) -> ExecutionRecord:
        with self._connect() as db:
            row = db.execute(
                "SELECT execution_id, context_sha256, state, created_at, updated_at, result_json, cleanup_confirmed FROM sandbox_executions WHERE execution_id=?",
                (execution_id,),
            ).fetchone()
        if row is None:
            raise ExecutionJournalError("execution_id not found")
        return self._record(row)

    def transition(self, execution_id: str, new_state: str, *, result: dict | None = None,
                   cleanup_confirmed: bool | None = None) -> ExecutionRecord:
        if new_state not in _ALLOWED_TRANSITIONS:
            raise ExecutionJournalError("unknown execution state")
        result_json = None
        if result is not None:
            try:
                result_json = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise ExecutionJournalError("result must be finite JSON data") from exc
            if len(result_json.encode("utf-8")) > 65536:
                raise ExecutionJournalError("result exceeds 64 KiB")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    "SELECT state FROM sandbox_executions WHERE execution_id=?", (execution_id,)
                ).fetchone()
                if row is None:
                    raise ExecutionJournalError("execution_id not found")
                old_state = row[0]
                if new_state not in _ALLOWED_TRANSITIONS[old_state]:
                    raise ExecutionJournalError(f"invalid transition: {old_state} -> {new_state}")
                now = self._now()
                cleanup = None if cleanup_confirmed is None else int(cleanup_confirmed)
                db.execute(
                    """UPDATE sandbox_executions
                       SET state=?, updated_at=?, result_json=COALESCE(?, result_json),
                           cleanup_confirmed=COALESCE(?, cleanup_confirmed)
                       WHERE execution_id=? AND state=?""",
                    (new_state, now, result_json, cleanup, execution_id, old_state),
                )
                if db.execute("SELECT changes()").fetchone()[0] != 1:
                    raise ExecutionJournalError("concurrent execution state change")
                db.execute("COMMIT")
            except Exception:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
        return self.get(execution_id)

    def mark_interrupted_for_reconciliation(self) -> tuple[str, ...]:
        """Return jobs requiring cleanup, including previously interrupted recovery.

        Existing cleanup_required rows remain in that state but are returned so
        recovery can resume after the recovery worker itself crashes.
        """
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                rows = db.execute(
                    "SELECT execution_id FROM sandbox_executions WHERE state IN ('created','running','cleanup_required') ORDER BY created_at"
                ).fetchall()
                ids = tuple(row[0] for row in rows)
                if ids:
                    now = self._now()
                    db.executemany(
                        "UPDATE sandbox_executions SET state='cleanup_required', updated_at=? WHERE execution_id=? AND state IN ('created','running')",
                        [(now, execution_id) for execution_id in ids],
                    )
                db.execute("COMMIT")
                return ids
            except Exception:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
