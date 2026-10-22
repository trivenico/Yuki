"""Operator reconciliation records for ambiguous durable executions.

This module never replays an action or rewrites the execution ledger. It stores an
operator's evidence-based disposition separately so ambiguous external effects
cannot be retried merely because a UI marked them resolved.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3


_ALLOWED_DISPOSITIONS = frozenset({
    "confirmed_completed",
    "confirmed_not_executed",
    "unresolved",
})


class SQLiteExecutionReconciliation:
    def __init__(self, database_path: str, *, ledger, audit_log):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path must be a non-empty path.")
        if not callable(getattr(ledger, "list_reconciliation_required", None)) or not callable(getattr(ledger, "get", None)):
            raise TypeError("A durable execution ledger is required.")
        if not callable(getattr(audit_log, "append", None)):
            raise TypeError("A durable audit log is required.")
        self.database_path = str(Path(database_path).expanduser())
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self.ledger = ledger
        self.audit_log = audit_log
        with closing(self._connect()) as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS execution_reconciliation (
                    idempotency_key TEXT PRIMARY KEY,
                    disposition TEXT NOT NULL CHECK (disposition IN
                        ('confirmed_completed', 'confirmed_not_executed', 'unresolved')),
                    operator TEXT NOT NULL,
                    evidence TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0, isolation_level=None)
        db.execute("PRAGMA busy_timeout = 5000")
        return db

    def list_pending(self, *, limit: int = 100):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000.")
        records = self.ledger.list_reconciliation_required(limit=limit)
        with closing(self._connect()) as db:
            resolved = {row[0] for row in db.execute(
                "SELECT idempotency_key FROM execution_reconciliation WHERE disposition != 'unresolved'"
            )}
        return [record for record in records if record.idempotency_key not in resolved]

    def resolve(self, idempotency_key: str, *, operator: str, disposition: str, evidence: str):
        if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 200:
            raise ValueError("A valid execution key is required.")
        if not isinstance(operator, str) or not operator.strip() or len(operator) > 256:
            raise ValueError("A valid operator identity is required.")
        if disposition not in _ALLOWED_DISPOSITIONS:
            raise ValueError("Unsupported reconciliation disposition.")
        if not isinstance(evidence, str) or len(evidence.strip()) < 12 or len(evidence) > 4000:
            raise ValueError("Evidence must contain 12-4000 characters.")
        record = self.ledger.get(idempotency_key)
        if record is None or record.state not in {"prepared", "running", "uncertain"}:
            raise ValueError("Execution is not awaiting reconciliation.")
        evidence = evidence.strip()
        created_at = datetime.now(timezone.utc).isoformat()
        # The audit entry is written first. If the reconciliation DB write then
        # fails, the audit still records the attempted operator decision.
        self.audit_log.append(
            event_type="execution_uncertain",
            pending_id=idempotency_key,
            actor=operator.strip(),
            skill_id="reconciliation:" + disposition,
        )
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute(
                    "INSERT INTO execution_reconciliation "
                    "(idempotency_key, disposition, operator, evidence, created_at) VALUES (?, ?, ?, ?, ?)",
                    (idempotency_key, disposition, operator.strip(), evidence, created_at),
                )
                db.execute("COMMIT")
            except sqlite3.IntegrityError as exc:
                db.execute("ROLLBACK")
                raise ValueError("This execution already has a reconciliation decision.") from exc
            except Exception:
                db.execute("ROLLBACK")
                raise
        return {
            "idempotency_key": idempotency_key,
            "disposition": disposition,
            "operator": operator.strip(),
            "evidence": evidence,
            "created_at": created_at,
            "replay_permitted": False,
        }

    def get_resolution(self, idempotency_key: str):
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT idempotency_key, disposition, operator, evidence, created_at "
                "FROM execution_reconciliation WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        if row is None:
            return None
        return dict(zip(("idempotency_key", "disposition", "operator", "evidence", "created_at"), row))
