"""SQLite-backed atomic provider governance for multiple Yuki processes.

Uses BEGIN IMMEDIATE for cross-process admission and persisted wall-clock lease
expiry. Keep the database on a filesystem with reliable SQLite locking; this
is not a substitute for a network database in multi-host deployments.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import math
from pathlib import Path
import re
import sqlite3
import time
import uuid
from typing import Callable

_PROVIDER = re.compile(r"^[a-z][a-z0-9_.-]{0,47}$")
_AGENT = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


class DurableBudgetExceeded(RuntimeError):
    """Durable provider admission was rejected by a configured limit."""


class DurableCircuitOpen(RuntimeError):
    """Durable provider circuit is open."""


class DurableDuplicateExecution(RuntimeError):
    """An idempotency key already identifies an existing execution."""

    def __init__(self, execution_id: str, status: str, *, fingerprint_matches: bool):
        self.execution_id = execution_id
        self.status = status
        self.fingerprint_matches = fingerprint_matches
        super().__init__("idempotency_key_already_used")


class DurableIdempotencyConflict(RuntimeError):
    """An idempotency key was reused with different request metadata."""


@dataclass(frozen=True)
class DurableLease:
    lease_id: str
    provider_id: str
    agent_id: str
    expires_at: float
    execution_id: str


@dataclass(frozen=True)
class DurableCircuitSnapshot:
    provider_id: str
    state: str
    consecutive_failures: int
    retry_after_seconds: float


class SQLiteProviderGovernanceStore:
    """Atomic provider circuit, concurrency, rate-window and lease store."""

    def __init__(
        self, database_path: str, *, max_global_concurrent: int = 32,
        max_provider_concurrent: int = 8, max_agent_concurrent: int = 2,
        max_agent_requests_per_window: int = 60, window_seconds: float = 60,
        lease_timeout_seconds: float = 300, failure_threshold: int = 5,
        reset_timeout_seconds: float = 30, clock: Callable[[], float] = time.time,
    ) -> None:
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path_required")
        for name, value, upper in (
            ("max_global_concurrent", max_global_concurrent, 100_000),
            ("max_provider_concurrent", max_provider_concurrent, 10_000),
            ("max_agent_concurrent", max_agent_concurrent, 10_000),
            ("max_agent_requests_per_window", max_agent_requests_per_window, 1_000_000),
            ("failure_threshold", failure_threshold, 100),
        ):
            if type(value) is not int or not 1 <= value <= upper:
                raise ValueError(name + "_out_of_range")
        for name, value, upper in (
            ("window_seconds", window_seconds, 86_400),
            ("lease_timeout_seconds", lease_timeout_seconds, 86_400),
            ("reset_timeout_seconds", reset_timeout_seconds, 3600),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0.1 <= value <= upper:
                raise ValueError(name + "_out_of_range")
        if not callable(clock):
            raise TypeError("clock_must_be_callable")
        self.database_path = str(Path(database_path).expanduser())
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self.max_global_concurrent = max_global_concurrent
        self.max_provider_concurrent = max_provider_concurrent
        self.max_agent_concurrent = max_agent_concurrent
        self.max_agent_requests_per_window = max_agent_requests_per_window
        self.window_seconds = float(window_seconds)
        self.lease_timeout_seconds = float(lease_timeout_seconds)
        self.failure_threshold = failure_threshold
        self.reset_timeout_seconds = float(reset_timeout_seconds)
        self._clock = clock
        setup = sqlite3.connect(self.database_path, timeout=10.0)
        try:
            setup.execute("PRAGMA journal_mode=WAL")
            setup.execute("PRAGMA synchronous=FULL")
        finally:
            setup.close()
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS provider_execution_records (
                    execution_id TEXT PRIMARY KEY,
                    lease_id TEXT NOT NULL UNIQUE,
                    provider_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN
                        ('running', 'succeeded', 'failed', 'cancelled', 'uncertain')),
                    reason TEXT NOT NULL,
                    started_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS provider_idempotency_keys (
                    provider_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_fingerprint TEXT NOT NULL,
                    execution_id TEXT NOT NULL UNIQUE,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(provider_id, agent_id, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS provider_execution_resolutions (
                    resolution_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    execution_id TEXT NOT NULL UNIQUE,
                    outcome TEXT NOT NULL CHECK(outcome IN ('succeeded', 'failed', 'cancelled')),
                    evidence_source TEXT NOT NULL,
                    evidence_sha256 TEXT NOT NULL,
                    provider_reference TEXT NOT NULL,
                    resolver_id TEXT NOT NULL,
                    resolved_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS provider_recovery_attempts (
                    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    execution_id TEXT NOT NULL,
                    operation_id TEXT,
                    actor_id TEXT NOT NULL,
                    action TEXT NOT NULL CHECK(action IN ('inspect', 'reconcile', 'defer')),
                    result TEXT NOT NULL CHECK(result IN ('started', 'completed', 'blocked', 'failed')),
                    reason_code TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS provider_workflow_correlations (
                    correlation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    execution_id TEXT NOT NULL UNIQUE,
                    operation_id TEXT NOT NULL UNIQUE,
                    provider_id TEXT NOT NULL,
                    linked_by TEXT NOT NULL,
                    linked_at REAL NOT NULL,
                    FOREIGN KEY(execution_id) REFERENCES provider_execution_records(execution_id)
                );
                CREATE INDEX IF NOT EXISTS idx_provider_workflow_correlations_operation
                    ON provider_workflow_correlations(operation_id);
                CREATE INDEX IF NOT EXISTS idx_provider_recovery_attempts_execution
                    ON provider_recovery_attempts(execution_id, attempt_id);
                CREATE TRIGGER IF NOT EXISTS provider_recovery_attempts_no_update
                    BEFORE UPDATE ON provider_recovery_attempts
                    BEGIN SELECT RAISE(ABORT, 'provider recovery attempts are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS provider_recovery_attempts_no_delete
                    BEFORE DELETE ON provider_recovery_attempts
                    BEGIN SELECT RAISE(ABORT, 'provider recovery attempts are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS provider_resolutions_no_update
                    BEFORE UPDATE ON provider_execution_resolutions
                    BEGIN SELECT RAISE(ABORT, 'provider execution resolutions are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS provider_resolutions_no_delete
                    BEFORE DELETE ON provider_execution_resolutions
                    BEGIN SELECT RAISE(ABORT, 'provider execution resolutions are immutable'); END;
                CREATE INDEX IF NOT EXISTS idx_provider_executions_status
                    ON provider_execution_records(provider_id, status, updated_at);
                CREATE TABLE IF NOT EXISTS provider_circuits (
                    provider_id TEXT PRIMARY KEY,
                    failures INTEGER NOT NULL DEFAULT 0 CHECK(failures >= 0),
                    opened_until REAL,
                    probe_lease_id TEXT
                );
                CREATE TABLE IF NOT EXISTS provider_leases (
                    lease_id TEXT PRIMARY KEY,
                    provider_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    expires_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_provider_leases_expiry
                    ON provider_leases(expires_at);
                CREATE INDEX IF NOT EXISTS idx_provider_leases_provider
                    ON provider_leases(provider_id);
                CREATE INDEX IF NOT EXISTS idx_provider_leases_agent
                    ON provider_leases(agent_id);
                CREATE TABLE IF NOT EXISTS provider_agent_windows (
                    agent_id TEXT PRIMARY KEY,
                    window_started REAL NOT NULL,
                    request_count INTEGER NOT NULL CHECK(request_count >= 0)
                );
                CREATE TABLE IF NOT EXISTS provider_governance_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    lease_id TEXT,
                    event_type TEXT NOT NULL CHECK(event_type IN
                        ('admitted', 'released', 'succeeded', 'failed', 'cancelled', 'expired')),
                    reason TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_provider_events_provider
                    ON provider_governance_events(provider_id, event_id);
                CREATE TRIGGER IF NOT EXISTS provider_events_no_update
                    BEFORE UPDATE ON provider_governance_events
                    BEGIN SELECT RAISE(ABORT, 'provider governance events are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS provider_events_no_delete
                    BEFORE DELETE ON provider_governance_events
                    BEGIN SELECT RAISE(ABORT, 'provider governance events are immutable'); END;
            """)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=10.0, isolation_level=None)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=10000")
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _validate_ids(provider_id: str, agent_id: str) -> None:
        if not isinstance(provider_id, str) or not _PROVIDER.fullmatch(provider_id):
            raise ValueError("provider_id_invalid")
        if not isinstance(agent_id, str) or not _AGENT.fullmatch(agent_id):
            raise ValueError("agent_id_invalid")

    def _event(self, db, provider_id, agent_id, lease_id, event_type, reason, now):
        db.execute(
            "INSERT INTO provider_governance_events "
            "(provider_id, agent_id, lease_id, event_type, reason, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (provider_id, agent_id, lease_id, event_type, reason, now),
        )

    def _purge_expired(self, db, now):
        expired = db.execute(
            "SELECT lease_id, provider_id, agent_id FROM provider_leases WHERE expires_at <= ?", (now,)
        ).fetchall()
        for lease in expired:
            db.execute("DELETE FROM provider_leases WHERE lease_id=?", (lease["lease_id"],))
            db.execute(
                "UPDATE provider_circuits SET probe_lease_id=NULL "
                "WHERE provider_id=? AND probe_lease_id=?",
                (lease["provider_id"], lease["lease_id"]),
            )
            db.execute(
                "UPDATE provider_execution_records SET status='uncertain', reason='lease_expired', updated_at=? "
                "WHERE lease_id=? AND status='running'",
                (now, lease["lease_id"]),
            )
            self._event(db, lease["provider_id"], lease["agent_id"], lease["lease_id"],
                        "expired", "lease_expired", now)

    def acquire(
        self, *, provider_id: str, agent_id: str,
        idempotency_key: str | None = None,
        request_fingerprint: str | None = None,
    ) -> DurableLease:
        self._validate_ids(provider_id, agent_id)
        if idempotency_key is None:
            if request_fingerprint is not None:
                raise ValueError("request_fingerprint_requires_idempotency_key")
        else:
            if not isinstance(idempotency_key, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", idempotency_key):
                raise ValueError("idempotency_key_invalid")
            if not isinstance(request_fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", request_fingerprint):
                raise ValueError("request_fingerprint_invalid")
        now = self._clock()
        lease_id = uuid.uuid4().hex
        execution_id = uuid.uuid4().hex
        with self._connect() as db:
            self._purge_expired(db, now)
            if idempotency_key is not None:
                existing = db.execute(
                    "SELECT k.request_fingerprint, r.execution_id, r.status "
                    "FROM provider_idempotency_keys k JOIN provider_execution_records r "
                    "ON r.execution_id=k.execution_id WHERE k.provider_id=? AND k.agent_id=? AND k.idempotency_key=?",
                    (provider_id, agent_id, idempotency_key),
                ).fetchone()
                if existing is not None:
                    if existing["request_fingerprint"] != request_fingerprint:
                        raise DurableIdempotencyConflict("idempotency_key_request_mismatch")
                    raise DurableDuplicateExecution(
                        existing["execution_id"], existing["status"], fingerprint_matches=True,
                    )
            circuit = db.execute(
                "SELECT failures, opened_until, probe_lease_id FROM provider_circuits WHERE provider_id=?",
                (provider_id,),
            ).fetchone()
            if circuit and circuit["opened_until"] is not None:
                if now < circuit["opened_until"] or circuit["probe_lease_id"] is not None:
                    raise DurableCircuitOpen("provider_circuit_open")
            counts = db.execute("""
                SELECT COUNT(*) AS total,
                    SUM(CASE WHEN provider_id=? THEN 1 ELSE 0 END) AS provider_count,
                    SUM(CASE WHEN agent_id=? THEN 1 ELSE 0 END) AS agent_count
                FROM provider_leases
            """, (provider_id, agent_id)).fetchone()
            if counts["total"] >= self.max_global_concurrent:
                raise DurableBudgetExceeded("global_concurrency_limit")
            if (counts["provider_count"] or 0) >= self.max_provider_concurrent:
                raise DurableBudgetExceeded("provider_concurrency_limit")
            if (counts["agent_count"] or 0) >= self.max_agent_concurrent:
                raise DurableBudgetExceeded("agent_concurrency_limit")
            window = db.execute(
                "SELECT window_started, request_count FROM provider_agent_windows WHERE agent_id=?",
                (agent_id,),
            ).fetchone()
            if window is None or now - window["window_started"] >= self.window_seconds:
                started, used = now, 0
            else:
                started, used = window["window_started"], window["request_count"]
            if used >= self.max_agent_requests_per_window:
                raise DurableBudgetExceeded("agent_request_rate_limit")
            db.execute("""
                INSERT INTO provider_agent_windows(agent_id, window_started, request_count)
                VALUES (?, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    window_started=excluded.window_started, request_count=excluded.request_count
            """, (agent_id, started, used + 1))
            expires = now + self.lease_timeout_seconds
            db.execute(
                "INSERT INTO provider_leases(lease_id, provider_id, agent_id, expires_at) VALUES (?, ?, ?, ?)",
                (lease_id, provider_id, agent_id, expires),
            )
            if circuit and circuit["opened_until"] is not None:
                db.execute(
                    "UPDATE provider_circuits SET probe_lease_id=? WHERE provider_id=?",
                    (lease_id, provider_id),
                )
            else:
                db.execute(
                    "INSERT OR IGNORE INTO provider_circuits(provider_id) VALUES (?)", (provider_id,)
                )
            db.execute(
                "INSERT INTO provider_execution_records "
                "(execution_id, lease_id, provider_id, agent_id, status, reason, started_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'running', 'admitted', ?, ?)",
                (execution_id, lease_id, provider_id, agent_id, now, now),
            )
            if idempotency_key is not None:
                db.execute(
                    "INSERT INTO provider_idempotency_keys "
                    "(provider_id, agent_id, idempotency_key, request_fingerprint, execution_id, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (provider_id, agent_id, idempotency_key, request_fingerprint, execution_id, now),
                )
            self._event(db, provider_id, agent_id, lease_id, "admitted", "capacity_reserved", now)
        return DurableLease(lease_id, provider_id, agent_id, expires, execution_id)

    def record_outcome(self, lease_id: str, *, outcome: str, reason: str = "provider_result") -> bool:
        if not isinstance(lease_id, str) or not re.fullmatch(r"[0-9a-f]{32}", lease_id):
            raise ValueError("lease_id_invalid")
        if outcome not in {"succeeded", "failed", "cancelled"}:
            raise ValueError("outcome_invalid")
        if not isinstance(reason, str) or not reason or len(reason) > 80 or not re.fullmatch(r"[a-z0-9_.-]+", reason):
            raise ValueError("reason_invalid")
        now = self._clock()
        with self._connect() as db:
            lease = db.execute("SELECT * FROM provider_leases WHERE lease_id=?", (lease_id,)).fetchone()
            if lease is None:
                return False
            provider_id, agent_id = lease["provider_id"], lease["agent_id"]
            circuit = db.execute(
                "SELECT failures, opened_until, probe_lease_id FROM provider_circuits WHERE provider_id=?",
                (provider_id,),
            ).fetchone()
            if outcome == "failed":
                failures = (circuit["failures"] if circuit else 0) + 1
                opened = circuit["opened_until"]
                if opened is not None or failures >= self.failure_threshold:
                    opened = now + self.reset_timeout_seconds
                db.execute(
                    "INSERT INTO provider_circuits(provider_id, failures, opened_until, probe_lease_id) "
                    "VALUES (?, ?, ?, NULL) ON CONFLICT(provider_id) DO UPDATE SET "
                    "failures=excluded.failures, opened_until=excluded.opened_until, probe_lease_id=NULL",
                    (provider_id, failures, opened),
                )
            elif outcome == "succeeded":
                # Only the designated half-open probe may close an open circuit.
                if circuit and circuit["opened_until"] is not None:
                    if circuit["probe_lease_id"] == lease_id:
                        db.execute(
                            "UPDATE provider_circuits SET failures=0, opened_until=NULL, probe_lease_id=NULL "
                            "WHERE provider_id=?", (provider_id,),
                        )
                else:
                    db.execute(
                        "UPDATE provider_circuits SET failures=0 WHERE provider_id=?", (provider_id,)
                    )
            elif circuit and circuit["probe_lease_id"] == lease_id:
                db.execute(
                    "UPDATE provider_circuits SET probe_lease_id=NULL WHERE provider_id=?", (provider_id,)
                )
            db.execute("DELETE FROM provider_leases WHERE lease_id=?", (lease_id,))
            db.execute(
                "UPDATE provider_execution_records SET status=?, reason=?, updated_at=? "
                "WHERE lease_id=? AND status='running'",
                (outcome, reason, now, lease_id),
            )
            self._event(db, provider_id, agent_id, lease_id, outcome, reason, now)
        return True

    def release(self, lease_id: str) -> bool:
        return self.record_outcome(lease_id, outcome="cancelled", reason="reservation_released")

    def link_workflow_operation(
        self, *, execution_id: str, operation_id: str, provider_id: str, linked_by: str,
    ) -> dict[str, object]:
        """Create an explicit one-to-one execution/operation correlation.

        This store can validate provider execution ownership, but the caller
        must independently verify that the workflow operation exists and uses
        the same provider before creating the link.
        """
        if not isinstance(execution_id, str) or not re.fullmatch(r"[0-9a-f]{32}", execution_id):
            raise ValueError("execution_id_invalid")
        if not isinstance(operation_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", operation_id):
            raise ValueError("operation_id_invalid")
        if not isinstance(provider_id, str) or not _PROVIDER.fullmatch(provider_id):
            raise ValueError("provider_id_invalid")
        if not isinstance(linked_by, str) or not _AGENT.fullmatch(linked_by):
            raise ValueError("linked_by_invalid")
        with self._connect() as db:
            execution = db.execute(
                "SELECT provider_id FROM provider_execution_records WHERE execution_id=?",
                (execution_id,),
            ).fetchone()
            if execution is None:
                raise KeyError("provider execution not found")
            if execution["provider_id"] != provider_id:
                raise ValueError("provider_execution_identity_mismatch")
            existing = db.execute(
                "SELECT execution_id, operation_id, provider_id, linked_by, linked_at "
                "FROM provider_workflow_correlations WHERE execution_id=? OR operation_id=?",
                (execution_id, operation_id),
            ).fetchone()
            if existing is not None:
                if existing["execution_id"] == execution_id and existing["operation_id"] == operation_id and existing["provider_id"] == provider_id:
                    return dict(existing)
                raise ValueError("provider_workflow_correlation_conflict")
            cursor = db.execute(
                "INSERT INTO provider_workflow_correlations "
                "(execution_id, operation_id, provider_id, linked_by, linked_at) VALUES (?, ?, ?, ?, ?)",
                (execution_id, operation_id, provider_id, linked_by, self._clock()),
            )
            row = db.execute(
                "SELECT execution_id, operation_id, provider_id, linked_by, linked_at "
                "FROM provider_workflow_correlations WHERE correlation_id=?",
                (cursor.lastrowid,),
            ).fetchone()
            return dict(row)

    def get_workflow_correlation(self, *, execution_id: str | None = None,
                                 operation_id: str | None = None) -> dict[str, object] | None:
        if (execution_id is None) == (operation_id is None):
            raise ValueError("exactly_one_correlation_identifier_required")
        if execution_id is not None and (
            not isinstance(execution_id, str) or not re.fullmatch(r"[0-9a-f]{32}", execution_id)
        ):
            raise ValueError("execution_id_invalid")
        if operation_id is not None and (
            not isinstance(operation_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", operation_id)
        ):
            raise ValueError("operation_id_invalid")
        field, value = ("execution_id", execution_id) if execution_id is not None else ("operation_id", operation_id)
        with self._connect() as db:
            row = db.execute(
                "SELECT execution_id, operation_id, provider_id, linked_by, linked_at "
                f"FROM provider_workflow_correlations WHERE {field}=?", (value,),
            ).fetchone()
            return dict(row) if row else None

    def list_workflow_correlations(self, *, limit: int = 100, after_id: int = 0) -> list[dict[str, object]]:
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("limit_out_of_range")
        if type(after_id) is not int or after_id < 0:
            raise ValueError("after_id_invalid")
        with self._connect() as db:
            rows = db.execute(
                "SELECT correlation_id, execution_id, operation_id, provider_id, linked_by, linked_at "
                "FROM provider_workflow_correlations WHERE correlation_id>? "
                "ORDER BY correlation_id LIMIT ?", (after_id, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_execution(self, execution_id: str) -> dict[str, object] | None:
        """Return one provider execution's non-sensitive correlation metadata."""
        if not isinstance(execution_id, str) or not re.fullmatch(r"[0-9a-f]{32}", execution_id):
            raise ValueError("execution_id_invalid")
        with self._connect() as db:
            row = db.execute(
                "SELECT execution_id, lease_id, provider_id, agent_id, status, reason, started_at, updated_at "
                "FROM provider_execution_records WHERE execution_id=?",
                (execution_id,),
            ).fetchone()
            return dict(row) if row else None

    def list_executions(
        self, *, provider_id: str, statuses: tuple[str, ...] = ("running", "uncertain"),
        limit: int = 100,
    ) -> list[dict[str, object]]:
        if not isinstance(provider_id, str) or not _PROVIDER.fullmatch(provider_id):
            raise ValueError("provider_id_invalid")
        allowed = {"running", "succeeded", "failed", "cancelled", "uncertain"}
        if not isinstance(statuses, tuple) or not statuses or any(s not in allowed for s in statuses):
            raise ValueError("execution_statuses_invalid")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("limit_out_of_range")
        placeholders = ",".join("?" for _ in statuses)
        with self._connect() as db:
            self._purge_expired(db, self._clock())
            rows = db.execute(
                "SELECT execution_id, lease_id, provider_id, agent_id, status, reason, started_at, updated_at "
                f"FROM provider_execution_records WHERE provider_id=? AND status IN ({placeholders}) "
                "ORDER BY started_at DESC LIMIT ?",
                (provider_id, *statuses, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_recovery_queue(self, *, limit: int = 100, after: tuple[float, str] | None = None) -> list[dict[str, object]]:
        """List running/uncertain executions with stable keyset pagination."""
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("limit_out_of_range")
        if after is not None and (
            not isinstance(after, tuple) or len(after) != 2
            or type(after[0]) not in (int, float)
            or not isinstance(after[1], str)
            or not re.fullmatch(r"[0-9a-f]{32}", after[1])
        ):
            raise ValueError("recovery_cursor_invalid")
        with self._connect() as db:
            if after is None:
                rows = db.execute(
                    "SELECT execution_id, lease_id, provider_id, agent_id, status, reason, started_at, updated_at "
                    "FROM provider_execution_records WHERE status IN ('running','uncertain') "
                    "ORDER BY updated_at, execution_id LIMIT ?", (limit,),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT execution_id, lease_id, provider_id, agent_id, status, reason, started_at, updated_at "
                    "FROM provider_execution_records WHERE status IN ('running','uncertain') "
                    "AND (updated_at > ? OR (updated_at = ? AND execution_id > ?)) "
                    "ORDER BY updated_at, execution_id LIMIT ?",
                    (after[0], after[0], after[1], limit),
                ).fetchall()
            return [dict(row) for row in rows]

    def record_recovery_attempt(self, *, execution_id: str, actor_id: str, action: str,
                                result: str, reason_code: str, operation_id: str | None = None) -> int:
        """Append metadata-only audit event; does not perform recovery."""
        if not isinstance(execution_id, str) or not re.fullmatch(r"[0-9a-f]{32}", execution_id):
            raise ValueError("execution_id_invalid")
        if not isinstance(actor_id, str) or not _AGENT.fullmatch(actor_id):
            raise ValueError("actor_id_invalid")
        if action not in {"inspect", "reconcile", "defer"}:
            raise ValueError("recovery_action_invalid")
        if result not in {"started", "completed", "blocked", "failed"}:
            raise ValueError("recovery_result_invalid")
        if not isinstance(reason_code, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", reason_code):
            raise ValueError("reason_code_invalid")
        if operation_id is not None and (
            not isinstance(operation_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", operation_id)
        ):
            raise ValueError("operation_id_invalid")
        with self._connect() as db:
            if db.execute("SELECT 1 FROM provider_execution_records WHERE execution_id=?", (execution_id,)).fetchone() is None:
                raise KeyError("provider execution not found")
            cur = db.execute(
                "INSERT INTO provider_recovery_attempts "
                "(execution_id, operation_id, actor_id, action, result, reason_code, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (execution_id, operation_id, actor_id, action, result, reason_code, self._clock()),
            )
            return int(cur.lastrowid)

    def list_recovery_attempts(self, *, execution_id: str, limit: int = 100,
                               before_attempt_id: int | None = None) -> list[dict[str, object]]:
        if not isinstance(execution_id, str) or not re.fullmatch(r"[0-9a-f]{32}", execution_id):
            raise ValueError("execution_id_invalid")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("limit_out_of_range")
        if before_attempt_id is not None and (type(before_attempt_id) is not int or before_attempt_id < 1):
            raise ValueError("before_attempt_id_invalid")
        with self._connect() as db:
            if before_attempt_id is None:
                rows = db.execute(
                    "SELECT attempt_id, execution_id, operation_id, actor_id, action, result, reason_code, created_at "
                    "FROM provider_recovery_attempts WHERE execution_id=? ORDER BY attempt_id DESC LIMIT ?",
                    (execution_id, limit),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT attempt_id, execution_id, operation_id, actor_id, action, result, reason_code, created_at "
                    "FROM provider_recovery_attempts WHERE execution_id=? AND attempt_id < ? "
                    "ORDER BY attempt_id DESC LIMIT ?", (execution_id, before_attempt_id, limit),
                ).fetchall()
            return [dict(row) for row in rows]

    def resolve_uncertain_execution(
        self, execution_id: str, *, outcome: str, evidence_source: str,
        evidence_sha256: str, provider_reference: str, resolver_id: str,
    ) -> bool:
        """Resolve an uncertain provider execution using attributable evidence.

        Evidence hashes identify the evidence supplied by the trusted caller;
        they do not independently authenticate the provider or prove its claim.
        """
        if not isinstance(execution_id, str) or not re.fullmatch(r"[0-9a-f]{32}", execution_id):
            raise ValueError("execution_id_invalid")
        if outcome not in {"succeeded", "failed", "cancelled"}:
            raise ValueError("outcome_invalid")
        if not isinstance(evidence_source, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", evidence_source):
            raise ValueError("evidence_source_invalid")
        if not isinstance(evidence_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", evidence_sha256):
            raise ValueError("evidence_sha256_invalid")
        if not isinstance(provider_reference, str) or not provider_reference.strip() or len(provider_reference) > 256 or "\\x00" in provider_reference:
            raise ValueError("provider_reference_invalid")
        if not isinstance(resolver_id, str) or not _AGENT.fullmatch(resolver_id):
            raise ValueError("resolver_id_invalid")
        now = self._clock()
        with self._connect() as db:
            record = db.execute(
                "SELECT * FROM provider_execution_records WHERE execution_id=?", (execution_id,)
            ).fetchone()
            if record is None:
                return False
            if record["status"] != "uncertain":
                raise ValueError("execution_not_uncertain")
            changed = db.execute(
                "UPDATE provider_execution_records SET status=?, reason='evidence_reconciled', updated_at=? "
                "WHERE execution_id=? AND status='uncertain'",
                (outcome, now, execution_id),
            ).rowcount
            if changed != 1:
                raise RuntimeError("execution_resolution_conflict")
            db.execute(
                "INSERT INTO provider_execution_resolutions "
                "(execution_id, outcome, evidence_source, evidence_sha256, provider_reference, resolver_id, resolved_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (execution_id, outcome, evidence_source, evidence_sha256,
                 provider_reference.strip(), resolver_id, now),
            )
            self._event(
                db, record["provider_id"], record["agent_id"], record["lease_id"],
                outcome, "evidence_reconciled", now,
            )
        return True

    def list_resolutions(self, *, execution_id: str) -> list[dict[str, object]]:
        if not isinstance(execution_id, str) or not re.fullmatch(r"[0-9a-f]{32}", execution_id):
            raise ValueError("execution_id_invalid")
        with self._connect() as db:
            rows = db.execute(
                "SELECT execution_id, outcome, evidence_source, evidence_sha256, "
                "provider_reference, resolver_id, resolved_at "
                "FROM provider_execution_resolutions WHERE execution_id=?",
                (execution_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def snapshot(self, *, provider_id: str, agent_id: str) -> dict[str, int | str | float]:
        self._validate_ids(provider_id, agent_id)
        now = self._clock()
        with self._connect() as db:
            self._purge_expired(db, now)
            counts = db.execute("""
                SELECT COUNT(*) AS total,
                    SUM(CASE WHEN provider_id=? THEN 1 ELSE 0 END) AS provider_count,
                    SUM(CASE WHEN agent_id=? THEN 1 ELSE 0 END) AS agent_count
                FROM provider_leases
            """, (provider_id, agent_id)).fetchone()
            circuit = db.execute(
                "SELECT failures, opened_until, probe_lease_id FROM provider_circuits WHERE provider_id=?",
                (provider_id,),
            ).fetchone()
            window = db.execute(
                "SELECT window_started, request_count FROM provider_agent_windows WHERE agent_id=?",
                (agent_id,),
            ).fetchone()
            if not circuit or circuit["opened_until"] is None:
                state, retry = "closed", 0.0
            elif now < circuit["opened_until"] or circuit["probe_lease_id"] is not None:
                state, retry = "open", max(0.0, circuit["opened_until"] - now)
            else:
                state, retry = "half_open", 0.0
            used = 0 if not window or now - window["window_started"] >= self.window_seconds else window["request_count"]
            return {
                "provider_id": provider_id, "circuit_state": state,
                "consecutive_failures": circuit["failures"] if circuit else 0,
                "retry_after_seconds": retry,
                "global_active_requests": counts["total"],
                "provider_active_requests": counts["provider_count"] or 0,
                "agent_active_requests": counts["agent_count"] or 0,
                "agent_requests_in_window": used,
            }

    def list_events(self, *, provider_id: str, limit: int = 100) -> list[dict[str, object]]:
        if not isinstance(provider_id, str) or not _PROVIDER.fullmatch(provider_id):
            raise ValueError("provider_id_invalid")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("limit_out_of_range")
        with self._connect() as db:
            rows = db.execute(
                "SELECT event_id, provider_id, agent_id, lease_id, event_type, reason, created_at "
                "FROM provider_governance_events WHERE provider_id=? ORDER BY event_id DESC LIMIT ?",
                (provider_id, limit),
            ).fetchall()
            return [dict(row) for row in rows]


__all__ = [
    "DurableBudgetExceeded", "DurableCircuitOpen", "DurableCircuitSnapshot",
    "DurableDuplicateExecution", "DurableIdempotencyConflict",
    "DurableLease", "SQLiteProviderGovernanceStore",
]
