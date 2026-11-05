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


@dataclass(frozen=True)
class DurableLease:
    lease_id: str
    provider_id: str
    agent_id: str
    expires_at: float


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
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.executescript("""
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
            self._event(db, lease["provider_id"], lease["agent_id"], lease["lease_id"],
                        "expired", "lease_expired", now)

    def acquire(self, *, provider_id: str, agent_id: str) -> DurableLease:
        self._validate_ids(provider_id, agent_id)
        now = self._clock()
        lease_id = uuid.uuid4().hex
        with self._connect() as db:
            self._purge_expired(db, now)
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
            self._event(db, provider_id, agent_id, lease_id, "admitted", "capacity_reserved", now)
        return DurableLease(lease_id, provider_id, agent_id, expires)

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
            self._event(db, provider_id, agent_id, lease_id, outcome, reason, now)
        return True

    def release(self, lease_id: str) -> bool:
        return self.record_outcome(lease_id, outcome="cancelled", reason="reservation_released")

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
    "DurableLease", "SQLiteProviderGovernanceStore",
]
