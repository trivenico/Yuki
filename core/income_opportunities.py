"""Persistent owner-scoped pipeline for online income opportunities.

This module stores research leads and owner-entered estimates. It does not
discover platforms, verify platform policies, submit applications, or record
estimated amounts as realized revenue.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from core.sqlite_file_security import prepare_private_sqlite_path
import re
import sqlite3
import uuid
from urllib.parse import urlsplit


_STATES = frozenset({
    "discovered", "researching", "validated", "ready", "active",
    "paused", "rejected", "completed",
})
_TRANSITIONS = {
    "discovered": frozenset({"researching", "rejected"}),
    "researching": frozenset({"validated", "rejected"}),
    "validated": frozenset({"researching", "ready", "rejected"}),
    "ready": frozenset({"validated", "active", "rejected"}),
    "active": frozenset({"paused", "completed"}),
    "paused": frozenset({"active", "rejected"}),
    "rejected": frozenset(),
    "completed": frozenset(),
}
_EVIDENCE = frozenset({"unverified", "anecdotal", "multiple_sources", "official"})
_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{1,99}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _owner(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise ValueError("invalid_owner_id")
    return value


def _https_url(value: str) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("invalid_source_url")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("source_url_must_be_https_without_embedded_credentials")
    if parsed.port not in (None, 443):
        raise ValueError("source_url_port_not_allowed")
    return value


@dataclass(frozen=True)
class IncomeOpportunity:
    opportunity_id: str
    owner_id: str
    platform_id: str
    title: str
    category: str
    source_url: str
    description: str
    evidence_level: str
    status: str
    currency: str
    estimated_net_minor: int | None
    startup_cost_minor: int
    estimated_hours: float | None
    fit_score: int | None
    demand_score: int | None
    competition_score: int | None
    created_at: str
    updated_at: str


class SQLiteIncomeOpportunityStore:
    """Durable opportunity pipeline with owner isolation and explicit states."""

    def __init__(self, database_path: str, *, max_per_owner: int = 10000):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path_required")
        if type(max_per_owner) is not int or not 1 <= max_per_owner <= 100000:
            raise ValueError("invalid_max_per_owner")
        self.database_path = prepare_private_sqlite_path(database_path)
        self.max_per_owner = max_per_owner
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS income_opportunities (
                opportunity_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                platform_id TEXT NOT NULL,
                title TEXT NOT NULL,
                category TEXT NOT NULL,
                source_url TEXT NOT NULL,
                description TEXT NOT NULL,
                evidence_level TEXT NOT NULL,
                status TEXT NOT NULL,
                currency TEXT NOT NULL,
                estimated_net_minor INTEGER,
                startup_cost_minor INTEGER NOT NULL,
                estimated_hours REAL,
                fit_score INTEGER,
                demand_score INTEGER,
                competition_score INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""")
            db.execute("""CREATE INDEX IF NOT EXISTS idx_income_opportunities_owner_state
                ON income_opportunities(owner_id, status, updated_at DESC)""")
            db.execute("""CREATE INDEX IF NOT EXISTS idx_income_opportunities_platform
                ON income_opportunities(owner_id, platform_id, created_at DESC)""")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA busy_timeout=5000")
            yield db
            # Explicit BEGIN IMMEDIATE blocks in create/transition must commit.
            # Without this, closing sqlite3 rolls back those writes on every OS.
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _record(row) -> IncomeOpportunity | None:
        return IncomeOpportunity(**dict(row)) if row is not None else None

    @staticmethod
    def _score(value, name):
        if value is None:
            return None
        if type(value) is not int or not 0 <= value <= 100:
            raise ValueError("invalid_" + name)
        return value

    def create(
        self,
        owner_id: str,
        *,
        platform_id: str,
        title: str,
        category: str,
        source_url: str,
        description: str = "",
        evidence_level: str = "unverified",
        currency: str = "GBP",
        estimated_net_minor: int | None = None,
        startup_cost_minor: int = 0,
        estimated_hours: float | None = None,
        fit_score: int | None = None,
        demand_score: int | None = None,
        competition_score: int | None = None,
    ) -> IncomeOpportunity:
        owner_id = _owner(owner_id)
        if not isinstance(platform_id, str) or not _ID.fullmatch(platform_id):
            raise ValueError("invalid_platform_id")
        for name, value, limit in (("title", title, 200), ("category", category, 80)):
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise ValueError("invalid_" + name)
        if not isinstance(description, str) or len(description) > 5000:
            raise ValueError("invalid_description")
        source_url = _https_url(source_url)
        if evidence_level not in _EVIDENCE:
            raise ValueError("invalid_evidence_level")
        if not isinstance(currency, str) or not _CURRENCY.fullmatch(currency):
            raise ValueError("invalid_currency")
        if estimated_net_minor is not None and (
            type(estimated_net_minor) is not int or abs(estimated_net_minor) > 10**12
        ):
            raise ValueError("invalid_estimated_net_minor")
        if type(startup_cost_minor) is not int or not 0 <= startup_cost_minor <= 10**12:
            raise ValueError("invalid_startup_cost_minor")
        if estimated_hours is not None and (
            isinstance(estimated_hours, bool)
            or not isinstance(estimated_hours, (int, float))
            or not 0 < estimated_hours <= 100000
        ):
            raise ValueError("invalid_estimated_hours")
        fit_score = self._score(fit_score, "fit_score")
        demand_score = self._score(demand_score, "demand_score")
        competition_score = self._score(competition_score, "competition_score")
        now = _now()
        opportunity_id = uuid.uuid4().hex
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute(
                "SELECT COUNT(*) FROM income_opportunities WHERE owner_id=?", (owner_id,)
            ).fetchone()[0]
            if count >= self.max_per_owner:
                raise ValueError("owner_opportunity_capacity_reached")
            db.execute("""INSERT INTO income_opportunities (
                opportunity_id, owner_id, platform_id, title, category, source_url,
                description, evidence_level, status, currency, estimated_net_minor,
                startup_cost_minor, estimated_hours, fit_score, demand_score,
                competition_score, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'discovered', ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
                opportunity_id, owner_id, platform_id, title.strip(), category.strip(),
                source_url, description, evidence_level, currency, estimated_net_minor,
                startup_cost_minor, float(estimated_hours) if estimated_hours is not None else None,
                fit_score, demand_score, competition_score, now, now,
            ))
            row = db.execute(
                "SELECT * FROM income_opportunities WHERE opportunity_id=?", (opportunity_id,)
            ).fetchone()
        return self._record(row)

    def get(self, owner_id: str, opportunity_id: str) -> IncomeOpportunity | None:
        owner_id = _owner(owner_id)
        if not isinstance(opportunity_id, str) or not re.fullmatch(r"[0-9a-f]{32}", opportunity_id):
            return None
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM income_opportunities WHERE owner_id=? AND opportunity_id=?",
                (owner_id, opportunity_id),
            ).fetchone()
        return self._record(row)

    def list(
        self, owner_id: str, *, status: str | None = None, platform_id: str | None = None,
        limit: int = 100,
    ) -> list[IncomeOpportunity]:
        owner_id = _owner(owner_id)
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("invalid_limit")
        if status is not None and status not in _STATES:
            raise ValueError("invalid_status")
        if platform_id is not None and (
            not isinstance(platform_id, str) or not _ID.fullmatch(platform_id)
        ):
            raise ValueError("invalid_platform_id")
        clauses = ["owner_id=?"]
        args: list[object] = [owner_id]
        if status is not None:
            clauses.append("status=?")
            args.append(status)
        if platform_id is not None:
            clauses.append("platform_id=?")
            args.append(platform_id)
        args.append(limit)
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM income_opportunities WHERE "
                + " AND ".join(clauses)
                + " ORDER BY updated_at DESC, opportunity_id LIMIT ?",
                args,
            ).fetchall()
        return [self._record(row) for row in rows]

    def transition(
        self, owner_id: str, opportunity_id: str, *, new_status: str,
    ) -> IncomeOpportunity | None:
        owner_id = _owner(owner_id)
        if new_status not in _STATES:
            raise ValueError("invalid_status")
        if not isinstance(opportunity_id, str) or not re.fullmatch(r"[0-9a-f]{32}", opportunity_id):
            return None
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM income_opportunities WHERE owner_id=? AND opportunity_id=?",
                (owner_id, opportunity_id),
            ).fetchone()
            if row is None:
                return None
            current = row["status"]
            if new_status not in _TRANSITIONS[current]:
                raise ValueError("invalid_opportunity_transition")
            db.execute(
                "UPDATE income_opportunities SET status=?, updated_at=? "
                "WHERE owner_id=? AND opportunity_id=? AND status=?",
                (new_status, now, owner_id, opportunity_id, current),
            )
            updated = db.execute(
                "SELECT * FROM income_opportunities WHERE owner_id=? AND opportunity_id=?",
                (owner_id, opportunity_id),
            ).fetchone()
        return self._record(updated)

    def delete(self, owner_id: str, opportunity_id: str) -> bool:
        owner_id = _owner(owner_id)
        if not isinstance(opportunity_id, str) or not re.fullmatch(r"[0-9a-f]{32}", opportunity_id):
            return False
        with self._connect() as db:
            cursor = db.execute(
                "DELETE FROM income_opportunities WHERE owner_id=? AND opportunity_id=?",
                (owner_id, opportunity_id),
            )
            return cursor.rowcount == 1
