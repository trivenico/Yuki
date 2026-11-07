"""Durable immutable execution-budget contracts and atomic usage reservations.

Budgets are snapshots, not mutable configuration. Usage is accounted through
idempotent reservations in SQLite transactions so concurrent workers sharing
this database cannot spend the same remaining allowance twice.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
from typing import Callable, Mapping

_RESOURCES = {
    "model_turns": "max_model_turns",
    "tool_calls": "max_tool_calls",
    "duration_ms": "max_duration_ms",
    "transcript_chars": "max_transcript_chars",
}
_LIMITS = {
    "max_model_turns": 100,
    "max_tool_calls": 500,
    "max_duration_ms": 3_600_000,
    "max_transcript_chars": 1_000_000,
}
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class ExecutionBudgetStoreError(ValueError):
    """Invalid, conflicting, or exhausted execution budget operation."""


@dataclass(frozen=True)
class ExecutionBudgetSnapshot:
    budget_id: str
    task_id: str
    parent_budget_id: str | None
    policy_version: str
    limits: dict[str, int]
    digest: str
    created_at: float


class ExecutionBudgetStore:
    """SQLite store for immutable budget snapshots and idempotent spend events."""

    def __init__(self, database_path: str, *, clock: Callable[[], float] = time.time):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path_required")
        if not callable(clock):
            raise TypeError("clock_must_be_callable")
        self.database_path = database_path
        self.clock = clock
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS execution_budget_snapshots (
                    budget_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL UNIQUE,
                    parent_budget_id TEXT,
                    policy_version TEXT NOT NULL,
                    limits_json TEXT NOT NULL,
                    digest TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_execution_budget_parent
                    ON execution_budget_snapshots(parent_budget_id);
                CREATE TABLE IF NOT EXISTS execution_budget_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    budget_id TEXT NOT NULL REFERENCES execution_budget_snapshots(budget_id),
                    idempotency_key TEXT NOT NULL,
                    resource TEXT NOT NULL CHECK(resource IN
                        ('model_turns','tool_calls','duration_ms','transcript_chars')),
                    amount INTEGER NOT NULL CHECK(amount > 0),
                    created_at REAL NOT NULL,
                    UNIQUE(budget_id, idempotency_key)
                );
                CREATE INDEX IF NOT EXISTS idx_execution_budget_events_budget
                    ON execution_budget_events(budget_id, resource);
                CREATE TRIGGER IF NOT EXISTS execution_budget_snapshot_no_update
                    BEFORE UPDATE ON execution_budget_snapshots
                    BEGIN SELECT RAISE(ABORT, 'budget snapshots are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS execution_budget_snapshot_no_delete
                    BEFORE DELETE ON execution_budget_snapshots
                    BEGIN SELECT RAISE(ABORT, 'budget snapshots are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS execution_budget_event_no_update
                    BEFORE UPDATE ON execution_budget_events
                    BEGIN SELECT RAISE(ABORT, 'budget events are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS execution_budget_event_no_delete
                    BEFORE DELETE ON execution_budget_events
                    BEGIN SELECT RAISE(ABORT, 'budget events are append-only'); END;
            """)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=10, isolation_level=None)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA busy_timeout=10000")
            yield db
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _id(value: str, field: str) -> str:
        if not isinstance(value, str) or not _ID.fullmatch(value):
            raise ExecutionBudgetStoreError(field + "_invalid")
        return value

    @staticmethod
    def _normalize_limits(limits: Mapping[str, int]) -> dict[str, int]:
        if not isinstance(limits, Mapping) or set(limits) != set(_LIMITS):
            raise ExecutionBudgetStoreError("budget_limits_invalid")
        normalized = {}
        for name, maximum in _LIMITS.items():
            value = limits[name]
            if type(value) is not int or not 1 <= value <= maximum:
                raise ExecutionBudgetStoreError("budget_limit_invalid:" + name)
            normalized[name] = value
        return normalized

    def create_snapshot(
        self, *, budget_id: str, task_id: str, limits: Mapping[str, int],
        policy_version: str, parent_budget_id: str | None = None,
    ) -> ExecutionBudgetSnapshot:
        budget_id = self._id(budget_id, "budget_id")
        task_id = self._id(task_id, "task_id")
        if parent_budget_id is not None:
            parent_budget_id = self._id(parent_budget_id, "parent_budget_id")
        if not isinstance(policy_version, str) or not policy_version.strip() or len(policy_version) > 64:
            raise ExecutionBudgetStoreError("policy_version_invalid")
        normalized = self._normalize_limits(limits)
        encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        now = float(self.clock())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if parent_budget_id is not None:
                parent = db.execute(
                    "SELECT budget_id FROM execution_budget_snapshots WHERE budget_id=?",
                    (parent_budget_id,),
                ).fetchone()
                if parent is None:
                    db.rollback()
                    raise ExecutionBudgetStoreError("parent_budget_not_found")
            try:
                db.execute(
                    "INSERT INTO execution_budget_snapshots "
                    "(budget_id,task_id,parent_budget_id,policy_version,limits_json,digest,created_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (budget_id,task_id,parent_budget_id,policy_version,encoded,digest,now),
                )
            except sqlite3.IntegrityError as exc:
                db.rollback()
                raise ExecutionBudgetStoreError("budget_or_task_already_snapshotted") from exc
            db.commit()
        return ExecutionBudgetSnapshot(budget_id, task_id, parent_budget_id,
                                        policy_version, normalized, digest, now)

    def get_snapshot(self, budget_id: str) -> ExecutionBudgetSnapshot:
        budget_id = self._id(budget_id, "budget_id")
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM execution_budget_snapshots WHERE budget_id=?", (budget_id,)
            ).fetchone()
        if row is None:
            raise KeyError("budget_snapshot_not_found")
        limits = json.loads(row["limits_json"])
        expected = hashlib.sha256(
            json.dumps(limits, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if expected != row["digest"]:
            raise ExecutionBudgetStoreError("budget_snapshot_integrity_failure")
        return ExecutionBudgetSnapshot(
            row["budget_id"], row["task_id"], row["parent_budget_id"],
            row["policy_version"], limits, row["digest"], row["created_at"],
        )

    def usage(self, budget_id: str) -> dict[str, int]:
        self.get_snapshot(budget_id)  # also validates the immutable snapshot digest
        with self._connect() as db:
            rows = db.execute(
                "SELECT resource, COALESCE(SUM(amount),0) AS total "
                "FROM execution_budget_events WHERE budget_id=? GROUP BY resource",
                (budget_id,),
            ).fetchall()
        return {resource: next((int(row["total"]) for row in rows
                                if row["resource"] == resource), 0)
                for resource in _RESOURCES}

    def reserve(
        self, *, budget_id: str, resource: str, amount: int,
        idempotency_key: str,
    ) -> dict[str, int]:
        budget_id = self._id(budget_id, "budget_id")
        idempotency_key = self._id(idempotency_key, "idempotency_key")
        if resource not in _RESOURCES:
            raise ExecutionBudgetStoreError("resource_invalid")
        if type(amount) is not int or amount < 1 or amount > 2**31 - 1:
            raise ExecutionBudgetStoreError("amount_invalid")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT limits_json FROM execution_budget_snapshots WHERE budget_id=?",
                (budget_id,),
            ).fetchone()
            if row is None:
                db.rollback()
                raise KeyError("budget_snapshot_not_found")
            existing = db.execute(
                "SELECT resource,amount FROM execution_budget_events "
                "WHERE budget_id=? AND idempotency_key=?",
                (budget_id,idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing["resource"] != resource or int(existing["amount"]) != amount:
                    db.rollback()
                    raise ExecutionBudgetStoreError("idempotency_key_conflict")
                db.commit()
                return self._usage_in_transaction(db, budget_id)
            limits = json.loads(row["limits_json"])
            limit = limits[_RESOURCES[resource]]
            used = db.execute(
                "SELECT COALESCE(SUM(amount),0) AS total FROM execution_budget_events "
                "WHERE budget_id=? AND resource=?",
                (budget_id,resource),
            ).fetchone()["total"]
            if int(used) + amount > limit:
                db.rollback()
                raise ExecutionBudgetStoreError("budget_exhausted:" + resource)
            db.execute(
                "INSERT INTO execution_budget_events "
                "(budget_id,idempotency_key,resource,amount,created_at) VALUES (?,?,?,?,?)",
                (budget_id,idempotency_key,resource,amount,float(self.clock())),
            )
            result = self._usage_in_transaction(db, budget_id)
            db.commit()
            return result

    @staticmethod
    def _usage_in_transaction(db, budget_id: str) -> dict[str, int]:
        rows = db.execute(
            "SELECT resource, COALESCE(SUM(amount),0) AS total "
            "FROM execution_budget_events WHERE budget_id=? GROUP BY resource",
            (budget_id,),
        ).fetchall()
        return {resource: next((int(row["total"]) for row in rows
                                if row["resource"] == resource), 0)
                for resource in _RESOURCES}
