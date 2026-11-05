"""SQLite persistence for privacy-safe model invocation metadata.

This store accepts only the versioned metadata contract. It intentionally has
no API for updating or deleting individual invocation records.
"""
from __future__ import annotations

import math
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

_PROVIDER_ID = re.compile(r"^[a-z][a-z0-9_.-]{0,47}$")
_TOKEN = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_ALLOWED_FIELDS = {
    "schema_version", "invocation_id", "provider_id", "model_id",
    "started_at", "duration_ms", "outcome", "message_count", "input_chars",
    "tool_count", "response_chars", "tool_call_count", "error_type",
}


def _validate_record(record: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(record, Mapping) or set(record) != _ALLOWED_FIELDS:
        raise ValueError("invalid_invocation_record_fields")
    value = dict(record)
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("unsupported_invocation_schema")
    if not isinstance(value["invocation_id"], str) or not _TOKEN.fullmatch(value["invocation_id"]):
        raise ValueError("invalid_invocation_id")
    if not isinstance(value["provider_id"], str) or not _PROVIDER_ID.fullmatch(value["provider_id"]):
        raise ValueError("invalid_provider_id")
    model_id = value["model_id"]
    if (not isinstance(model_id, str) or not model_id.strip()
            or len(model_id) > 160 or any(ord(ch) < 32 for ch in model_id)):
        raise ValueError("invalid_model_id")
    if not isinstance(value["started_at"], str) or len(value["started_at"]) > 64:
        raise ValueError("invalid_started_at")
    try:
        parsed = datetime.fromisoformat(value["started_at"])
    except ValueError as exc:
        raise ValueError("invalid_started_at") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("started_at_must_be_timezone_aware")
    duration = value["duration_ms"]
    if isinstance(duration, bool) or not isinstance(duration, (int, float)):
        raise ValueError("invalid_duration_ms")
    if not math.isfinite(duration) or duration < 0 or duration > 86_400_000:
        raise ValueError("duration_ms_out_of_range")
    if not isinstance(value["outcome"], str) or value["outcome"] not in {"success", "error"}:
        raise ValueError("invalid_invocation_outcome")
    for field in ("message_count", "input_chars", "tool_count",
                  "response_chars", "tool_call_count"):
        number = value[field]
        if type(number) is not int or not 0 <= number <= 10_000_000:
            raise ValueError("invalid_" + field)
    error_type = value["error_type"]
    if value["outcome"] == "success" and error_type is not None:
        raise ValueError("success_must_not_include_error_type")
    if value["outcome"] == "error":
        if not isinstance(error_type, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", error_type):
            raise ValueError("invalid_error_type")
    return value


class InvocationTraceStore:
    """Thread-safe, append-only SQLite store for invocation metadata."""

    def __init__(self, database_path: str):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path_required")
        self.database_path = str(Path(database_path).expanduser())
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS model_invocations (
                    invocation_id TEXT PRIMARY KEY,
                    schema_version INTEGER NOT NULL CHECK(schema_version = 1),
                    provider_id TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    duration_ms REAL NOT NULL CHECK(duration_ms >= 0),
                    outcome TEXT NOT NULL CHECK(outcome IN ('success', 'error')),
                    message_count INTEGER NOT NULL CHECK(message_count >= 0),
                    input_chars INTEGER NOT NULL CHECK(input_chars >= 0),
                    tool_count INTEGER NOT NULL CHECK(tool_count >= 0),
                    response_chars INTEGER NOT NULL CHECK(response_chars >= 0),
                    tool_call_count INTEGER NOT NULL CHECK(tool_call_count >= 0),
                    error_type TEXT
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_model_invocations_provider_started
                ON model_invocations(provider_id, started_at DESC)
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_model_invocations_model_started
                ON model_invocations(model_id, started_at DESC)
            """)
            db.execute("""
                CREATE TRIGGER IF NOT EXISTS model_invocations_no_update
                BEFORE UPDATE ON model_invocations
                BEGIN SELECT RAISE(ABORT, 'invocation_records_are_append_only'); END
            """)
            db.execute("""
                CREATE TRIGGER IF NOT EXISTS model_invocations_no_delete
                BEFORE DELETE ON model_invocations
                BEGIN SELECT RAISE(ABORT, 'invocation_records_are_append_only'); END
            """)

    @contextmanager
    def _connect(self):
        """Yield a configured connection and always close its file handles.

        sqlite3.Connection's context manager commits/rolls back but does not
        close the connection. Explicit closure is important on Windows, where
        open SQLite handles prevent temporary database cleanup.
        """
        db = sqlite3.connect(self.database_path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            # synchronous is connection-scoped; apply it to every connection.
            db.execute("PRAGMA synchronous=FULL")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def append(self, record: Mapping[str, Any]) -> None:
        value = _validate_record(record)
        with self._connect() as db:
            db.execute("""
                INSERT INTO model_invocations (
                    invocation_id, schema_version, provider_id, model_id,
                    started_at, duration_ms, outcome, message_count, input_chars,
                    tool_count, response_chars, tool_call_count, error_type
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                value["invocation_id"], value["schema_version"], value["provider_id"],
                value["model_id"], value["started_at"], value["duration_ms"],
                value["outcome"], value["message_count"], value["input_chars"],
                value["tool_count"], value["response_chars"], value["tool_call_count"],
                value["error_type"],
            ))

    def list_recent(
        self, *, limit: int = 50, provider_id: str | None = None,
        model_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("limit_must_be_1_to_500")
        clauses = []
        params: list[Any] = []
        if provider_id is not None:
            if not isinstance(provider_id, str) or not _PROVIDER_ID.fullmatch(provider_id):
                raise ValueError("invalid_provider_id")
            clauses.append("provider_id = ?")
            params.append(provider_id)
        if model_id is not None:
            if (not isinstance(model_id, str) or not model_id.strip()
                    or len(model_id) > 160 or any(ord(ch) < 32 for ch in model_id)):
                raise ValueError("invalid_model_id")
            clauses.append("model_id = ?")
            params.append(model_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM model_invocations" + where +
                " ORDER BY started_at DESC, invocation_id DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        return [dict(row) for row in rows]
