"""Durable journal for side-effecting tool actions.

The journal coordinates dispatch attempts; it cannot make a remote provider
idempotent. Once execution may have begun, callers must reconcile uncertain
outcomes rather than automatically replaying the action.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
import secrets
import sqlite3
from typing import Any

from core.sqlite_file_security import prepare_private_sqlite_path


class ToolActionJournalError(RuntimeError):
    """Base error for durable tool-action journal operations."""


class ToolActionConflict(ToolActionJournalError):
    """The idempotency key was reused with a different request or state."""


class ToolActionTransitionError(ToolActionJournalError):
    """The requested action-state transition is not valid."""


_KEY = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
_TOOL = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")
_STATES = frozenset({"prepared", "executing", "succeeded", "outcome_unknown", "not_executed"})
_MAX_REQUEST_BYTES = 32 * 1024
_MAX_RESULT_BYTES = 16 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_bytes(value: Any, limit: int, label: str) -> tuple[str, bytes]:
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        raw = encoded.encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ValueError(f"{label} must be JSON-serializable.") from exc
    if len(raw) > limit:
        raise ValueError(f"{label} exceeds the {limit}-byte limit.")
    return encoded, raw


@dataclass(frozen=True)
class ToolAction:
    action_id: str
    idempotency_key: str
    tool_name: str
    request_digest: str
    state: str
    result: Any
    error_code: str | None
    created_at: str
    updated_at: str
    version: int


class ToolActionJournal:
    """SQLite CAS journal that prevents blind replay after uncertain dispatch."""

    def __init__(self, database_path: str):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path must be a non-empty path.")
        self.database_path = prepare_private_sqlite_path(database_path)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS tool_actions (
                    action_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    tool_name TEXT NOT NULL,
                    request_digest TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN
                        ('prepared','executing','succeeded','outcome_unknown','not_executed')),
                    result_json TEXT,
                    error_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 0
                )
            """)
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_tool_actions_state_updated "
                "ON tool_actions(state, updated_at)"
            )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.database_path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        return db

    @staticmethod
    def _record(row: sqlite3.Row) -> ToolAction:
        result = json.loads(row["result_json"]) if row["result_json"] is not None else None
        return ToolAction(
            action_id=row["action_id"],
            idempotency_key=row["idempotency_key"],
            tool_name=row["tool_name"],
            request_digest=row["request_digest"],
            state=row["state"],
            result=result,
            error_code=row["error_code"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            version=row["version"],
        )

    @staticmethod
    def _validate_key(key: str) -> None:
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            raise ValueError("idempotency_key must be a bounded stable identifier.")

    @staticmethod
    def _validate_tool(tool_name: str) -> None:
        if not isinstance(tool_name, str) or not _TOOL.fullmatch(tool_name):
            raise ValueError("tool_name must be a valid registered tool identifier.")

    def get(self, idempotency_key: str) -> ToolAction | None:
        self._validate_key(idempotency_key)
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM tool_actions WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
        return self._record(row) if row is not None else None

    def reserve(self, idempotency_key: str, tool_name: str, request: Any) -> ToolAction:
        """Create a prepared action or return the same-key, same-payload record."""
        self._validate_key(idempotency_key)
        self._validate_tool(tool_name)
        _, raw = _json_bytes(request, _MAX_REQUEST_BYTES, "request")
        digest = hashlib.sha256(raw).hexdigest()
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute(
                    """INSERT INTO tool_actions
                       (action_id,idempotency_key,tool_name,request_digest,state,
                        created_at,updated_at,version)
                       VALUES (?,?,?,?, 'prepared',?,?,0)
                       ON CONFLICT(idempotency_key) DO NOTHING""",
                    (secrets.token_hex(16), idempotency_key, tool_name, digest, now, now),
                )
                row = db.execute(
                    "SELECT * FROM tool_actions WHERE idempotency_key=?",
                    (idempotency_key,),
                ).fetchone()
                if row["tool_name"] != tool_name or row["request_digest"] != digest:
                    raise ToolActionConflict("idempotency key is already bound to a different action.")
                db.commit()
            except Exception:
                db.rollback()
                raise
        return self._record(row)

    def begin_execution(self, idempotency_key: str, request_digest: str) -> ToolAction:
        """Atomically grant the one dispatch attempt for a prepared action."""
        self._validate_key(idempotency_key)
        if not isinstance(request_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", request_digest):
            raise ValueError("request_digest must be a SHA-256 hex digest.")
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    "SELECT * FROM tool_actions WHERE idempotency_key=?",
                    (idempotency_key,),
                ).fetchone()
                if row is None or row["request_digest"] != request_digest:
                    raise ToolActionConflict("action does not exist for this request digest.")
                if row["state"] == "succeeded":
                    db.commit()
                    return self._record(row)
                if row["state"] != "prepared":
                    raise ToolActionTransitionError(
                        "action is not dispatchable; reconcile uncertain outcomes before retrying."
                    )
                db.execute(
                    """UPDATE tool_actions SET state='executing',updated_at=?,version=version+1
                       WHERE idempotency_key=? AND state='prepared' AND version=?""",
                    (now, idempotency_key, row["version"]),
                )
                updated = db.execute(
                    "SELECT * FROM tool_actions WHERE idempotency_key=?",
                    (idempotency_key,),
                ).fetchone()
                db.commit()
            except Exception:
                db.rollback()
                raise
        return self._record(updated)

    def _transition(
        self, idempotency_key: str, expected_state: str, new_state: str,
        *, result: Any = None, error_code: str | None = None,
    ) -> ToolAction:
        self._validate_key(idempotency_key)
        result_json = None
        if new_state == "succeeded":
            result_json, _ = _json_bytes(result, _MAX_RESULT_BYTES, "result")
        if error_code is not None and (
            not isinstance(error_code, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", error_code)
        ):
            raise ValueError("error_code must be a bounded lowercase identifier.")
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    "SELECT * FROM tool_actions WHERE idempotency_key=?",
                    (idempotency_key,),
                ).fetchone()
                if row is None or row["state"] != expected_state:
                    raise ToolActionTransitionError(
                        f"action must be in {expected_state!r} state to transition."
                    )
                db.execute(
                    """UPDATE tool_actions SET state=?,result_json=?,error_code=?,
                       updated_at=?,version=version+1
                       WHERE idempotency_key=? AND state=? AND version=?""",
                    (new_state, result_json, error_code, now, idempotency_key,
                     expected_state, row["version"]),
                )
                updated = db.execute(
                    "SELECT * FROM tool_actions WHERE idempotency_key=?",
                    (idempotency_key,),
                ).fetchone()
                db.commit()
            except Exception:
                db.rollback()
                raise
        return self._record(updated)

    def mark_succeeded(self, idempotency_key: str, result: Any) -> ToolAction:
        return self._transition(idempotency_key, "executing", "succeeded", result=result)

    def mark_outcome_unknown(self, idempotency_key: str, error_code: str = "dispatch_interrupted") -> ToolAction:
        """Record that a side effect may have happened; this state is never auto-replayed."""
        return self._transition(
            idempotency_key, "executing", "outcome_unknown", error_code=error_code,
        )

    def mark_not_executed(self, idempotency_key: str, error_code: str) -> ToolAction:
        """Use only when the adapter can prove no external side effect occurred."""
        return self._transition(
            idempotency_key, "executing", "not_executed", error_code=error_code,
        )

    def reconcile_succeeded(self, idempotency_key: str, result: Any) -> ToolAction:
        """Resolve an uncertain action after authoritative provider reconciliation."""
        return self._transition(
            idempotency_key, "outcome_unknown", "succeeded", result=result,
        )

    def reconcile_not_executed(self, idempotency_key: str, error_code: str) -> ToolAction:
        """Resolve an uncertain action only after proving the side effect did not occur."""
        return self._transition(
            idempotency_key, "outcome_unknown", "not_executed", error_code=error_code,
        )
