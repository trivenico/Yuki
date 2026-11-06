"""Durable, idempotent provider usage and cost ledger.

Provider-reported token counts are exact only when marked as such by the caller.
Costs are calculated only when an explicit, versioned price is supplied; unknown
prices remain NULL and are never guessed.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import re
import sqlite3
from pathlib import Path
from typing import Any
import uuid


class UsageLedgerError(RuntimeError):
    """Base class for usage ledger failures."""


class UsageConflict(UsageLedgerError):
    """An idempotency key was reused with different usage data."""


@dataclass(frozen=True)
class UsageRecord:
    event_id: str
    request_id: str | None
    session_id: str
    agent_id: str | None
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    estimated: bool
    input_price_per_million: str | None
    output_price_per_million: str | None
    currency: str | None
    price_version: str | None
    cost: str | None
    created_at: str


class UsageLedger:
    """SQLite-backed usage ledger with idempotent writes and exact decimal costs."""

    MAX_TOKENS = 10_000_000
    MAX_TEXT = 160
    _CURRENCY = re.compile(r"^[A-Z]{3}$")

    def __init__(self, path: str | Path, *, timeout_seconds: float = 5.0) -> None:
        if path is None:
            raise ValueError("ledger_path_required")
        self.path = Path(path).expanduser().resolve()
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not 0.1 <= timeout_seconds <= 60:
            raise ValueError("timeout_seconds_out_of_range")
        self.timeout_seconds = float(timeout_seconds)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=self.timeout_seconds)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS provider_usage_events (
                    event_id TEXT PRIMARY KEY,
                    request_id TEXT UNIQUE,
                    session_id TEXT NOT NULL,
                    agent_id TEXT,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL CHECK(input_tokens >= 0),
                    output_tokens INTEGER NOT NULL CHECK(output_tokens >= 0),
                    estimated INTEGER NOT NULL CHECK(estimated IN (0, 1)),
                    input_price_per_million TEXT,
                    output_price_per_million TEXT,
                    currency TEXT,
                    price_version TEXT,
                    cost TEXT,
                    created_at TEXT NOT NULL
                )
            """)
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_usage_session_created "
                "ON provider_usage_events(session_id, created_at)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_usage_provider_created "
                "ON provider_usage_events(provider, created_at)"
            )

    @classmethod
    def _text(cls, value: Any, label: str, *, optional: bool = False) -> str | None:
        if value is None and optional:
            return None
        if not isinstance(value, str) or not value.strip() or len(value) > cls.MAX_TEXT:
            raise ValueError(label + "_invalid")
        return value.strip()

    @classmethod
    def _tokens(cls, value: Any, label: str) -> int:
        if type(value) is not int or not 0 <= value <= cls.MAX_TOKENS:
            raise ValueError(label + "_out_of_range")
        return value

    @staticmethod
    def _price(value: Any, label: str) -> Decimal | None:
        if value is None:
            return None
        if isinstance(value, bool):
            raise ValueError(label + "_invalid")
        try:
            price = Decimal(str(value))
        except (InvalidOperation, ValueError):
            raise ValueError(label + "_invalid") from None
        if not price.is_finite() or price < 0 or price > Decimal("1000000"):
            raise ValueError(label + "_out_of_range")
        return price

    def record(
        self, *, provider: str, model: str, input_tokens: int, output_tokens: int,
        session_id: str = "default", request_id: str | None = None,
        agent_id: str | None = None, estimated: bool = False,
        input_price_per_million: Any = None,
        output_price_per_million: Any = None,
        currency: str | None = None, price_version: str | None = None,
    ) -> dict[str, Any]:
        provider = self._text(provider, "provider")
        model = self._text(model, "model")
        session_id = self._text(session_id, "session_id")
        request_id = self._text(request_id, "request_id", optional=True)
        agent_id = self._text(agent_id, "agent_id", optional=True)
        input_tokens = self._tokens(input_tokens, "input_tokens")
        output_tokens = self._tokens(output_tokens, "output_tokens")
        if type(estimated) is not bool:
            raise ValueError("estimated_must_be_boolean")

        input_price = self._price(input_price_per_million, "input_price_per_million")
        output_price = self._price(output_price_per_million, "output_price_per_million")
        if (input_price is None) != (output_price is None):
            raise ValueError("both_input_and_output_prices_required")
        if input_price is None:
            if currency is not None or price_version is not None:
                raise ValueError("price_metadata_requires_prices")
            currency_value = version_value = cost = None
        else:
            if not isinstance(currency, str) or not self._CURRENCY.fullmatch(currency):
                raise ValueError("currency_must_be_iso_4217_code")
            currency_value = currency
            version_value = self._text(price_version, "price_version")
            cost = (
                Decimal(input_tokens) * input_price
                + Decimal(output_tokens) * output_price
            ) / Decimal(1_000_000)
            cost = format(cost, "f")

        event_id = uuid.uuid4().hex
        created_at = datetime.now(timezone.utc).isoformat()
        values = (
            event_id, request_id, session_id, agent_id, provider, model,
            input_tokens, output_tokens, int(estimated),
            None if input_price is None else format(input_price, "f"),
            None if output_price is None else format(output_price, "f"),
            currency_value, version_value, cost, created_at,
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if request_id is not None:
                existing = connection.execute(
                    "SELECT * FROM provider_usage_events WHERE request_id = ?",
                    (request_id,),
                ).fetchone()
                if existing is not None:
                    record = self._from_row(existing)
                    expected = {
                        "session_id": session_id, "agent_id": agent_id,
                        "provider": provider, "model": model,
                        "input_tokens": input_tokens, "output_tokens": output_tokens,
                        "estimated": estimated,
                        "input_price_per_million": values[9],
                        "output_price_per_million": values[10],
                        "currency": currency_value, "price_version": version_value,
                        "cost": cost,
                    }
                    actual = asdict(record)
                    if any(actual[key] != value for key, value in expected.items()):
                        raise UsageConflict("request_id_reused_with_different_usage")
                    return {"event": actual, "duplicate": True}
            connection.execute("""
                INSERT INTO provider_usage_events VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
            """, values)
            row = connection.execute(
                "SELECT * FROM provider_usage_events WHERE event_id = ?", (event_id,)
            ).fetchone()
            return {"event": asdict(self._from_row(row)), "duplicate": False}

    @staticmethod
    def _from_row(row: sqlite3.Row) -> UsageRecord:
        data = dict(row)
        data["estimated"] = bool(data["estimated"])
        return UsageRecord(**data)

    def summary(self, *, session_id: str | None = None) -> dict[str, Any]:
        where = " WHERE session_id = ?" if session_id is not None else ""
        params = (session_id,) if session_id is not None else ()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM provider_usage_events" + where + " ORDER BY created_at, event_id",
                params,
            ).fetchall()
        records = [self._from_row(row) for row in rows]
        totals: dict[str, Decimal] = {}
        unknown_cost_events = 0
        for item in records:
            if item.cost is None:
                unknown_cost_events += 1
            else:
                totals[item.currency] = totals.get(item.currency, Decimal(0)) + Decimal(item.cost)
        return {
            "events": len(records),
            "input_tokens": sum(x.input_tokens for x in records),
            "output_tokens": sum(x.output_tokens for x in records),
            "total_tokens": sum(x.input_tokens + x.output_tokens for x in records),
            "exact_events": sum(not x.estimated for x in records),
            "estimated_events": sum(x.estimated for x in records),
            "unknown_cost_events": unknown_cost_events,
            "cost_by_currency": {key: format(value, "f") for key, value in sorted(totals.items())},
        }

    def list_events(self, *, session_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit_out_of_range")
        query = "SELECT * FROM provider_usage_events"
        params: tuple[Any, ...] = ()
        if session_id is not None:
            query += " WHERE session_id = ?"
            params = (self._text(session_id, "session_id"),)
        query += " ORDER BY created_at DESC, event_id DESC LIMIT ?"
        params += (limit,)
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [asdict(self._from_row(row)) for row in rows]


__all__ = ["UsageConflict", "UsageLedger", "UsageLedgerError", "UsageRecord"]
