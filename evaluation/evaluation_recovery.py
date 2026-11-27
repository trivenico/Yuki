"""Explicit, owner-scoped recovery planning for orphaned evaluation links.

This module discovers candidate-bound runs without mutating stores by default.
Applying a plan is a separate explicit call. Owner IDs are labels, not identity.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import secrets
import sqlite3
from typing import Any

from core.sqlite_file_security import prepare_private_sqlite_path


@dataclass(frozen=True, slots=True)
class EvaluationRecoveryItem:
    run_id: str
    candidate_id: str
    status: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class EvaluationRecoveryPlan:
    owner_id: str
    created_at: str
    items: tuple[EvaluationRecoveryItem, ...]


class EvaluationRecoverySupervisor:
    """Plans and explicitly applies repair of missing candidate-evaluation links."""

    def __init__(self, audit_database_path: str | Path):
        if not isinstance(audit_database_path, (str, Path)) or not str(audit_database_path).strip():
            raise ValueError("audit_database_path must be a non-empty path")
        self.database_path = prepare_private_sqlite_path(audit_database_path)
        with closing(self._connect()) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS evaluation_recovery_events (
                    event_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            db.execute("""
                CREATE TRIGGER IF NOT EXISTS evaluation_recovery_events_no_update
                BEFORE UPDATE ON evaluation_recovery_events
                BEGIN SELECT RAISE(ABORT, 'recovery audit events are immutable'); END
            """)
            db.execute("""
                CREATE TRIGGER IF NOT EXISTS evaluation_recovery_events_no_delete
                BEFORE DELETE ON evaluation_recovery_events
                BEGIN SELECT RAISE(ABORT, 'recovery audit events are immutable'); END
            """)

    def _connect(self):
        self.database_path = prepare_private_sqlite_path(self.database_path)
        db = sqlite3.connect(self.database_path, timeout=5.0, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA synchronous=FULL")
        return db

    def _audit(self, *, owner_id: str, run_id: str, candidate_id: str,
               outcome: str, detail: str = "") -> None:
        if outcome not in {"planned", "repaired", "already_linked", "rejected"}:
            raise ValueError("unsupported recovery outcome")
        if not isinstance(detail, str) or len(detail) > 1000 or "\x00" in detail:
            raise ValueError("detail must be bounded text")
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO evaluation_recovery_events "
                "(event_id,owner_id,run_id,candidate_id,outcome,detail,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (secrets.token_urlsafe(18), owner_id, run_id, candidate_id,
                 outcome, detail, datetime.now(timezone.utc).isoformat()),
            )

    def plan(self, *, owner_id: str, run_store: Any, registry: Any,
             receipt_store: Any | None = None, limit: int = 100) -> EvaluationRecoveryPlan:
        if not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 200:
            raise ValueError("owner_id must be bounded non-empty text")
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        if not callable(getattr(run_store, "list_runs", None)):
            raise TypeError("run_store must provide list_runs")
        items: list[EvaluationRecoveryItem] = []
        for run in reversed(run_store.list_runs(owner_id=owner_id.strip(), limit=limit)):
            if not run.candidate_digest:
                continue
            try:
                links = registry.list_evaluations(
                    owner_id=owner_id.strip(), candidate_id=run.candidate_id,
                    run_store=run_store, receipt_store=receipt_store,
                )
                linked = next((item for item in links if item.run_id == run.run_id), None)
                if linked is not None:
                    status, detail = "already_linked", ""
                else:
                    candidate = registry.get(owner_id=owner_id.strip(), candidate_id=run.candidate_id)
                    if candidate.payload_digest != run.candidate_digest:
                        raise ValueError("candidate digest differs from evaluation run")
                    if candidate.state != "accepted_for_evaluation":
                        raise ValueError("candidate is not accepted_for_evaluation")
                    # Validate receipt binding during planning; no writes occur.
                    if run.execution_attempt_id:
                        if receipt_store is None:
                            raise ValueError("receipt_store is required for execution-backed run")
                        manifest, receipt = receipt_store.get(
                            owner_id=owner_id.strip(), attempt_id=run.execution_attempt_id
                        )
                        if (manifest.manifest_digest != run.execution_manifest_digest
                            or manifest.artifact_digest != run.execution_artifact_digest
                            or receipt.receipt_digest != run.execution_receipt_digest):
                            raise ValueError("execution receipt does not match persisted run")
                    status, detail = "planned", ""
            except Exception as exc:
                status, detail = "rejected", f"{type(exc).__name__}: {str(exc)[:900]}"
            item = EvaluationRecoveryItem(run.run_id, run.candidate_id, status, detail)
            items.append(item)
            if status == "planned":
                self._audit(owner_id=owner_id.strip(), run_id=run.run_id,
                            candidate_id=run.candidate_id, outcome="planned")
            elif status == "rejected":
                self._audit(owner_id=owner_id.strip(), run_id=run.run_id,
                            candidate_id=run.candidate_id, outcome="rejected", detail=detail)
        return EvaluationRecoveryPlan(owner_id.strip(), datetime.now(timezone.utc).isoformat(), tuple(items))

    def apply(self, *, plan: EvaluationRecoveryPlan, run_store: Any, registry: Any,
              receipt_store: Any | None = None) -> tuple[EvaluationRecoveryItem, ...]:
        """Apply only items marked planned in a previously reviewed plan."""
        if not isinstance(plan, EvaluationRecoveryPlan):
            raise TypeError("plan must be an EvaluationRecoveryPlan")
        results: list[EvaluationRecoveryItem] = []
        for item in plan.items:
            if item.status != "planned":
                results.append(item)
                continue
            try:
                link = registry.reconcile_evaluation_link(
                    owner_id=plan.owner_id, candidate_id=item.candidate_id,
                    run_id=item.run_id, run_store=run_store,
                    receipt_store=receipt_store,
                )
                outcome = "repaired"
                result = EvaluationRecoveryItem(item.run_id, item.candidate_id, outcome)
                if link.run_id != item.run_id:
                    raise RuntimeError("recovery returned a different run link")
                self._audit(owner_id=plan.owner_id, run_id=item.run_id,
                            candidate_id=item.candidate_id, outcome=outcome)
            except Exception as exc:
                detail = f"{type(exc).__name__}: {str(exc)[:900]}"
                result = EvaluationRecoveryItem(item.run_id, item.candidate_id, "rejected", detail)
                self._audit(owner_id=plan.owner_id, run_id=item.run_id,
                            candidate_id=item.candidate_id, outcome="rejected", detail=detail)
            results.append(result)
        return tuple(results)

    def list_events(self, *, owner_id: str, limit: int = 100) -> tuple[dict[str, str], ...]:
        if not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 200:
            raise ValueError("owner_id must be bounded non-empty text")
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        with closing(self._connect()) as db, db:
            rows = db.execute(
                "SELECT owner_id,run_id,candidate_id,outcome,detail,created_at "
                "FROM evaluation_recovery_events WHERE owner_id=? "
                "ORDER BY created_at,event_id LIMIT ?",
                (owner_id.strip(), limit),
            ).fetchall()
        return tuple(dict(row) for row in rows)
