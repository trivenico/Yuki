"""Local-first earning companion primitives for Yuki.

This module organizes goals, income opportunities, and recorded revenue. It does
not promise earnings, place trades, send applications, or contact third parties.
The user remains the decision-maker for consequential actions.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


GOAL_STATUSES = {"active", "paused", "completed"}
OPPORTUNITY_STATUSES = {"idea", "researching", "ready", "in_progress", "won", "lost", "archived"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _required_text(value: str, field: str, max_length: int = 500) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    value = value.strip()
    if len(value) > max_length:
        raise ValueError(f"{field} must be at most {max_length} characters")
    return value


def _amount(value: float, field: str = "amount") -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    value = float(value)
    if value < 0 or value != value or value in (float("inf"), float("-inf")):
        raise ValueError(f"{field} must be finite and non-negative")
    return round(value, 2)


@dataclass(frozen=True)
class EarningSummary:
    active_goals: int
    open_opportunities: int
    recorded_revenue: float
    currency: str
    updated_at: str


class EarningCompanion:
    """SQLite-backed, user-owned tracker for an online earning workflow.

    The caller chooses the database location. Keep it in Yuki's private workspace,
    apply filesystem permissions, and include it in user-controlled backups.
    """
    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path).expanduser()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS earning_goals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    target_amount REAL NOT NULL CHECK(target_amount >= 0),
                    currency TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('active','paused','completed')),
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS earning_opportunities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    next_action TEXT NOT NULL,
                    estimated_amount REAL NOT NULL CHECK(estimated_amount >= 0),
                    currency TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN
                        ('idea','researching','ready','in_progress','won','lost','archived')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS earning_revenue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    amount REAL NOT NULL CHECK(amount >= 0),
                    currency TEXT NOT NULL,
                    source TEXT NOT NULL,
                    received_on TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_goals_status ON earning_goals(status);
                CREATE INDEX IF NOT EXISTS idx_opportunities_status ON earning_opportunities(status);
                CREATE INDEX IF NOT EXISTS idx_revenue_currency_date ON earning_revenue(currency, received_on);
            """)

    def add_goal(self, title: str, target_amount: float, currency: str = "USD") -> int:
        title = _required_text(title, "title")
        target_amount = _amount(target_amount, "target_amount")
        currency = _required_text(currency, "currency", 3).upper()
        if len(currency) != 3 or not currency.isalpha():
            raise ValueError("currency must be a three-letter code")
        with self._connect() as db:
            cursor = db.execute(
                "INSERT INTO earning_goals(title,target_amount,currency,status,created_at) VALUES(?,?,?,?,?)",
                (title, target_amount, currency, "active", _now()),
            )
            return int(cursor.lastrowid)

    def set_goal_status(self, goal_id: int, status: str) -> None:
        if status not in GOAL_STATUSES:
            raise ValueError("unsupported goal status")
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE earning_goals SET status=? WHERE id=?", (status, int(goal_id))
            )
            if cursor.rowcount == 0:
                raise KeyError("goal not found")

    def add_opportunity(
        self,
        title: str,
        channel: str,
        next_action: str,
        estimated_amount: float = 0,
        currency: str = "USD",
        status: str = "idea",
    ) -> int:
        title = _required_text(title, "title")
        channel = _required_text(channel, "channel", 100)
        next_action = _required_text(next_action, "next_action")
        estimated_amount = _amount(estimated_amount, "estimated_amount")
        currency = _required_text(currency, "currency", 3).upper()
        if len(currency) != 3 or not currency.isalpha():
            raise ValueError("currency must be a three-letter code")
        if status not in OPPORTUNITY_STATUSES:
            raise ValueError("unsupported opportunity status")
        now = _now()
        with self._connect() as db:
            cursor = db.execute(
                """INSERT INTO earning_opportunities
                (title,channel,next_action,estimated_amount,currency,status,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?)""",
                (title, channel, next_action, estimated_amount, currency, status, now, now),
            )
            return int(cursor.lastrowid)

    def update_opportunity(self, opportunity_id: int, *, status: Optional[str] = None,
                           next_action: Optional[str] = None) -> None:
        if status is None and next_action is None:
            raise ValueError("provide status or next_action")
        fields, values = [], []
        if status is not None:
            if status not in OPPORTUNITY_STATUSES:
                raise ValueError("unsupported opportunity status")
            fields.append("status=?")
            values.append(status)
        if next_action is not None:
            fields.append("next_action=?")
            values.append(_required_text(next_action, "next_action"))
        fields.append("updated_at=?")
        values.append(_now())
        values.append(int(opportunity_id))
        with self._connect() as db:
            cursor = db.execute(
                f"UPDATE earning_opportunities SET {','.join(fields)} WHERE id=?", values
            )
            if cursor.rowcount == 0:
                raise KeyError("opportunity not found")

    def record_revenue(self, amount: float, source: str, received_on: str,
                       currency: str = "USD", note: str = "") -> int:
        amount = _amount(amount)
        source = _required_text(source, "source", 200)
        currency = _required_text(currency, "currency", 3).upper()
        note = note.strip() if isinstance(note, str) else ""
        if len(currency) != 3 or not currency.isalpha():
            raise ValueError("currency must be a three-letter code")
        try:
            datetime.strptime(received_on, "%Y-%m-%d")
        except (TypeError, ValueError) as exc:
            raise ValueError("received_on must use YYYY-MM-DD") from exc
        if len(note) > 1000:
            raise ValueError("note must be at most 1000 characters")
        with self._connect() as db:
            cursor = db.execute(
                """INSERT INTO earning_revenue
                (amount,currency,source,received_on,note,created_at) VALUES(?,?,?,?,?,?)""",
                (amount, currency, source, received_on, note, _now()),
            )
            return int(cursor.lastrowid)

    def summary(self, currency: str = "USD") -> EarningSummary:
        currency = _required_text(currency, "currency", 3).upper()
        if len(currency) != 3 or not currency.isalpha():
            raise ValueError("currency must be a three-letter code")
        with self._connect() as db:
            goals = db.execute(
                "SELECT COUNT(*) AS n FROM earning_goals WHERE status='active'"
            ).fetchone()["n"]
            opportunities = db.execute(
                """SELECT COUNT(*) AS n FROM earning_opportunities
                WHERE status IN ('idea','researching','ready','in_progress')"""
            ).fetchone()["n"]
            revenue = db.execute(
                "SELECT COALESCE(SUM(amount),0) AS total FROM earning_revenue WHERE currency=?",
                (currency,),
            ).fetchone()["total"]
        return EarningSummary(
            active_goals=int(goals),
            open_opportunities=int(opportunities),
            recorded_revenue=round(float(revenue), 2),
            currency=currency,
            updated_at=_now(),
        )
