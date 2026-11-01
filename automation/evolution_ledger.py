"""Append-only, hash-chained metadata ledger for supervised Yuki evolution.

The ledger stores operational metadata only. It must not receive prompts,
source contents, credentials, personal data, or arbitrary agent output.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import re
import sqlite3
from typing import Any


ZERO_HASH = "0" * 64
EVENT_TYPES = frozenset({
    "proposal_created", "plan_created", "approval_granted", "approval_denied",
    "execution_started", "execution_completed", "execution_failed",
    "review_completed", "verification_completed", "rollback_completed",
})
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")
_SAFE_DETAIL_KEY = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_ALLOWED_DETAIL_KEYS = frozenset({
    "component", "status", "reason_code", "commit_sha", "branch",
    "test_count", "passed_count", "failed_count", "duration_ms",
    "model_provider", "model_id", "review_id", "artifact_digest",
})
_MAX_DETAILS_BYTES = 4096


class EvolutionLedgerError(RuntimeError):
    """Base error for ledger operations."""


class EvolutionLedger:
    """SQLite append-only event journal with per-event SHA-256 chain hashes.

    Hash chaining detects accidental or unauthorized row modification, but a
    local administrator able to rewrite the entire database can recompute it.
    Production deployments should periodically export/anchor the head hash to
    an independently controlled immutable store.
    """

    def __init__(self, database_path: str | Path):
        if not isinstance(database_path, (str, Path)) or not str(database_path).strip():
            raise ValueError("database_path must be a non-empty path.")
        self.database_path = str(Path(database_path).expanduser())
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS evolution_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    occurred_at TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    actor_hash TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_evolution_events_request "
                       "ON evolution_events(request_id, sequence)")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0, isolation_level=None)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=5000")
            db.execute("PRAGMA synchronous=FULL")
            yield db
        finally:
            db.close()

    @staticmethod
    def _validate_identifier(value: str, name: str) -> str:
        if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
            raise ValueError(f"{name} must be a bounded safe identifier.")
        return value

    @classmethod
    def _sanitize_details(cls, details: dict[str, Any] | None) -> str:
        if details is None:
            details = {}
        if not isinstance(details, dict) or len(details) > len(_ALLOWED_DETAIL_KEYS):
            raise ValueError("details must be a small metadata dictionary.")
        sanitized: dict[str, Any] = {}
        for key, value in details.items():
            if not isinstance(key, str) or not _SAFE_DETAIL_KEY.fullmatch(key):
                raise ValueError("details contain an invalid key.")
            if key not in _ALLOWED_DETAIL_KEYS:
                raise ValueError(f"details key is not allowlisted: {key}")
            string_keys = {"status", "reason_code", "component", "model_provider", "model_id", "branch", "review_id", "commit_sha", "artifact_digest"}
            integer_keys = {"test_count", "passed_count", "failed_count", "duration_ms"}
            if key in string_keys:
                if not isinstance(value, str) or not value or len(value) > 200 or any(ord(ch) < 32 for ch in value):
                    raise ValueError(f"details string is invalid for {key}.")
            elif key in integer_keys:
                if type(value) is not int or value < 0:
                    raise ValueError(f"details value has the wrong type for {key}.")
            else:
                raise ValueError(f"details key is not allowlisted: {key}")
            else:
                raise ValueError(f"details value has the wrong type for {key}.")
            sanitized[key] = value
        encoded = json.dumps(sanitized, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        if len(encoded.encode("utf-8")) > _MAX_DETAILS_BYTES:
            raise ValueError("details exceed the size limit.")
        return encoded

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @classmethod
    def _event_hash(cls, *, sequence: int, event_id: str, occurred_at: str,
                    event_type: str, request_id: str, actor_hash: str,
                    details_json: str, previous_hash: str) -> str:
        canonical = json.dumps({
            "sequence": sequence, "event_id": event_id, "occurred_at": occurred_at,
            "event_type": event_type, "request_id": request_id,
            "actor_hash": actor_hash, "details_json": details_json,
            "previous_hash": previous_hash,
        }, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return cls._digest(canonical)

    def append(self, *, event_type: str, request_id: str, actor_id: str,
               details: dict[str, Any] | None = None) -> dict[str, Any]:
        if not isinstance(event_type, str) or event_type not in EVENT_TYPES:
            raise ValueError("unsupported evolution event type.")
        self._validate_identifier(request_id, "request_id")
        if not isinstance(actor_id, str) or not actor_id.strip() or len(actor_id) > 256:
            raise ValueError("actor_id must be non-empty bounded text.")
        if any(ord(ch) < 32 for ch in actor_id):
            raise ValueError("actor_id contains control characters.")
        details_json = self._sanitize_details(details)
        occurred_at = datetime.now(timezone.utc).isoformat()
        actor_hash = self._digest(actor_id)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                previous = db.execute(
                    "SELECT sequence, event_hash FROM evolution_events ORDER BY sequence DESC LIMIT 1"
                ).fetchone()
                sequence = (previous["sequence"] + 1) if previous else 1
                previous_hash = previous["event_hash"] if previous else ZERO_HASH
                event_id = f"evo-{sequence:012d}"
                event_hash = self._event_hash(
                    sequence=sequence, event_id=event_id, occurred_at=occurred_at,
                    event_type=event_type, request_id=request_id, actor_hash=actor_hash,
                    details_json=details_json, previous_hash=previous_hash,
                )
                db.execute("""
                    INSERT INTO evolution_events
                    (sequence, event_id, occurred_at, event_type, request_id,
                     actor_hash, details_json, previous_hash, event_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (sequence, event_id, occurred_at, event_type, request_id,
                      actor_hash, details_json, previous_hash, event_hash))
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise
        return {
            "sequence": sequence, "event_id": event_id, "occurred_at": occurred_at,
            "event_type": event_type, "request_id": request_id,
            "actor_hash": actor_hash, "details": json.loads(details_json),
            "previous_hash": previous_hash, "event_hash": event_hash,
        }

    def list_events(self, *, request_id: str | None = None,
                    limit: int = 100) -> list[dict[str, Any]]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be an integer from 1 to 1000.")
        with self._connect() as db:
            if request_id is None:
                rows = db.execute(
                    "SELECT * FROM evolution_events ORDER BY sequence DESC LIMIT ?", (limit,)
                ).fetchall()
            else:
                self._validate_identifier(request_id, "request_id")
                rows = db.execute(
                    "SELECT * FROM evolution_events WHERE request_id=? "
                    "ORDER BY sequence DESC LIMIT ?", (request_id, limit)
                ).fetchall()
        return [self._to_dict(row) for row in rows]

    @staticmethod
    def _to_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "sequence": row["sequence"], "event_id": row["event_id"],
            "occurred_at": row["occurred_at"], "event_type": row["event_type"],
            "request_id": row["request_id"], "actor_hash": row["actor_hash"],
            "details": json.loads(row["details_json"]),
            "previous_hash": row["previous_hash"], "event_hash": row["event_hash"],
        }

    def head(self) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute(
                "SELECT sequence, event_hash FROM evolution_events ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
        return {"sequence": row["sequence"], "event_hash": row["event_hash"]} if row else {
            "sequence": 0, "event_hash": ZERO_HASH,
        }

    def verify_chain(self) -> bool:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM evolution_events ORDER BY sequence ASC").fetchall()
        previous_hash = ZERO_HASH
        expected_sequence = 1
        for row in rows:
            if row["sequence"] != expected_sequence or row["previous_hash"] != previous_hash:
                return False
            try:
                details = self._sanitize_details(json.loads(row["details_json"]))
            except (TypeError, ValueError, json.JSONDecodeError):
                return False
            if details != row["details_json"]:
                return False
            expected = self._event_hash(
                sequence=row["sequence"], event_id=row["event_id"],
                occurred_at=row["occurred_at"], event_type=row["event_type"],
                request_id=row["request_id"], actor_hash=row["actor_hash"],
                details_json=row["details_json"], previous_hash=row["previous_hash"],
            )
            if not hmac.compare_digest(expected, row["event_hash"]):
                return False
            previous_hash = row["event_hash"]
            expected_sequence += 1
        return True
