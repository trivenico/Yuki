"""Durable journal for side-effecting tool actions.

The journal coordinates dispatch attempts; it cannot make a remote provider
idempotent. Once execution may have begun, callers must reconcile uncertain
outcomes rather than automatically replaying the action.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import secrets
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator

from core.sqlite_file_security import prepare_private_sqlite_path


class ToolActionJournalError(RuntimeError):
    """Base error for durable tool-action journal operations."""


class ToolActionConflict(ToolActionJournalError):
    """The idempotency key was reused with a different request or state."""


class ToolActionTransitionError(ToolActionJournalError):
    """The requested action-state transition is not valid."""


_KEY = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
_TOOL = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")
_STATES = frozenset({"prepared", "authorizing", "executing", "succeeded", "outcome_unknown", "not_executed"})
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
    lease_owner: str | None
    lease_expires_at: str | None


class ToolActionJournal:
    """SQLite CAS journal that prevents blind replay after uncertain dispatch."""

    def __init__(self, database_path: str):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path must be a non-empty path.")
        self.database_path = prepare_private_sqlite_path(database_path)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            # Schema creation, legacy CHECK-constraint rebuild, row copy, and
            # index recreation must commit as one unit. Closing an interrupted
            # connection rolls back the entire migration rather than leaving a
            # renamed or partially copied action table behind.
            db.execute("BEGIN IMMEDIATE")
            db.execute("""
                CREATE TABLE IF NOT EXISTS tool_actions (
                    action_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    tool_name TEXT NOT NULL,
                    request_digest TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN
                        ('prepared','authorizing','executing','succeeded','outcome_unknown','not_executed')),
                    result_json TEXT,
                    error_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 0,
                    lease_owner TEXT,
                    lease_expires_at TEXT
                )
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(tool_actions)")}
            for column in ("lease_owner", "lease_expires_at"):
                if column not in columns:
                    db.execute("ALTER TABLE tool_actions ADD COLUMN " + column + " TEXT")
            schema_row = db.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='tool_actions'"
            ).fetchone()
            if schema_row is not None and "'authorizing'" not in (schema_row["sql"] or ""):
                # Upgrade the legacy CHECK constraint without dropping durable actions.
                db.execute("DROP INDEX IF EXISTS idx_tool_actions_state_updated")
                db.execute("ALTER TABLE tool_actions RENAME TO tool_actions_legacy_authorizing")
                db.execute("""CREATE TABLE tool_actions (
                    action_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    tool_name TEXT NOT NULL,
                    request_digest TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN
                        ('prepared','authorizing','executing','succeeded','outcome_unknown','not_executed')),
                    result_json TEXT,
                    error_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 0,
                    lease_owner TEXT,
                    lease_expires_at TEXT
                )""")
                db.execute("""INSERT INTO tool_actions
                    (action_id,idempotency_key,tool_name,request_digest,state,result_json,
                     error_code,created_at,updated_at,version,lease_owner,lease_expires_at)
                    SELECT action_id,idempotency_key,tool_name,request_digest,state,result_json,
                     error_code,created_at,updated_at,version,lease_owner,lease_expires_at
                    FROM tool_actions_legacy_authorizing""")
                db.execute("DROP TABLE tool_actions_legacy_authorizing")
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_tool_actions_state_updated "
                "ON tool_actions(state, updated_at)"
            )
            db.execute("COMMIT")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Open a short-lived connection and always release its OS file handles."""
        db = sqlite3.connect(self.database_path, timeout=10, isolation_level=None)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA busy_timeout=10000")
            yield db
        finally:
            db.close()

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
            lease_owner=row["lease_owner"],
            lease_expires_at=row["lease_expires_at"],
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

    def reserve_in_transaction(
        self, db: sqlite3.Connection, idempotency_key: str, tool_name: str,
        request: Any, *, action_id: str,
    ) -> ToolAction:
        """Reserve an action inside a caller-owned transaction on this database.

        The caller must hold a write transaction and commit/rollback it. This
        lets higher-level coordinators atomically persist related state without
        opening a second SQLite transaction.
        """
        if not isinstance(db, sqlite3.Connection):
            raise TypeError("db must be a SQLite connection")
        self._validate_key(idempotency_key)
        self._validate_tool(tool_name)
        _, raw = _json_bytes(request, _MAX_REQUEST_BYTES, "request")
        digest = hashlib.sha256(raw).hexdigest()
        if not isinstance(action_id, str) or not re.fullmatch(r"[0-9a-f]{32}", action_id):
            raise ValueError("action_id must be a 32-character lowercase hex identifier.")
        now = _now()
        db.row_factory = sqlite3.Row
        db.execute(
            """INSERT INTO tool_actions
               (action_id,idempotency_key,tool_name,request_digest,state,
                created_at,updated_at,version)
               VALUES (?,?,?,?, 'prepared',?,?,1)
               ON CONFLICT(idempotency_key) DO NOTHING""",
            (action_id, idempotency_key, tool_name, digest, now, now),
        )
        row = db.execute(
            "SELECT * FROM tool_actions WHERE idempotency_key=?",
            (idempotency_key,),
        ).fetchone()
        if (
            row["tool_name"] != tool_name
            or row["request_digest"] != digest
            or row["action_id"] != action_id
        ):
            raise ToolActionConflict("idempotency key is already bound to a different action.")
        if row["version"] < 1 or row["state"] != "prepared":
            raise ToolActionConflict(
                "existing action is not a safely reusable atomic pre-dispatch reservation."
            )
        return self._record(row)

    def reserve(
        self, idempotency_key: str, tool_name: str, request: Any, *, action_id: str | None = None,
    ) -> ToolAction:
        """Create a prepared action or return the same-key, same-payload record."""
        self._validate_key(idempotency_key)
        self._validate_tool(tool_name)
        _, raw = _json_bytes(request, _MAX_REQUEST_BYTES, "request")
        digest = hashlib.sha256(raw).hexdigest()
        if action_id is not None and (
            not isinstance(action_id, str) or not re.fullmatch(r"[0-9a-f]{32}", action_id)
        ):
            raise ValueError("action_id must be a 32-character lowercase hex identifier.")
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
                    (action_id or secrets.token_hex(16), idempotency_key, tool_name, digest, now, now),
                )
                row = db.execute(
                    "SELECT * FROM tool_actions WHERE idempotency_key=?",
                    (idempotency_key,),
                ).fetchone()
                if (
                    row["tool_name"] != tool_name
                    or row["request_digest"] != digest
                    or (action_id is not None and row["action_id"] != action_id)
                ):
                    raise ToolActionConflict("idempotency key is already bound to a different action.")
                db.commit()
            except Exception:
                db.rollback()
                raise
        return self._record(row)

    def claim_execution(
        self, idempotency_key: str, request_digest: str, *, lease_seconds: int = 120,
    ) -> ToolAction:
        """Exclusively claim authorization; no external dispatch is permitted in this state."""
        self._validate_key(idempotency_key)
        if not isinstance(request_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", request_digest):
            raise ValueError("request_digest must be a SHA-256 hex digest.")
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or not 5 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be an integer from 5 to 3600.")
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        expires = (now_dt + timedelta(seconds=lease_seconds)).isoformat()
        owner = secrets.token_urlsafe(24)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("SELECT * FROM tool_actions WHERE idempotency_key=?", (idempotency_key,)).fetchone()
                if row is None or row["request_digest"] != request_digest:
                    raise ToolActionConflict("action does not exist for this request digest.")
                if row["state"] == "succeeded":
                    db.commit()
                    return self._record(row)
                if row["state"] != "prepared":
                    raise ToolActionTransitionError("action is already claimed, executing, or requires reconciliation.")
                db.execute(
                    """UPDATE tool_actions SET state='authorizing',updated_at=?,version=version+1,
                       lease_owner=?,lease_expires_at=?
                       WHERE idempotency_key=? AND state='prepared' AND version=?""",
                    (now, owner, expires, idempotency_key, row["version"]),
                )
                updated = db.execute("SELECT * FROM tool_actions WHERE idempotency_key=?", (idempotency_key,)).fetchone()
                db.commit()
            except Exception:
                db.rollback()
                raise
        return self._record(updated)

    def begin_claimed_execution(self, idempotency_key: str, lease_owner: str) -> ToolAction:
        """Promote an authorized claim to dispatchable execution after approval succeeds."""
        self._validate_key(idempotency_key)
        if not isinstance(lease_owner, str) or not lease_owner:
            raise ValueError("lease_owner must be a non-empty fencing token.")
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("SELECT * FROM tool_actions WHERE idempotency_key=?", (idempotency_key,)).fetchone()
                if row is None:
                    raise ToolActionTransitionError("action claim does not exist.")
                if row["state"] == "succeeded":
                    db.commit()
                    return self._record(row)
                if row["state"] != "authorizing" or row["lease_owner"] != lease_owner or row["lease_expires_at"] <= now:
                    raise ToolActionTransitionError("authorization claim is no longer owned by this worker.")
                db.execute(
                    """UPDATE tool_actions SET state='executing',updated_at=?,version=version+1
                       WHERE idempotency_key=? AND state='authorizing' AND lease_owner=? AND version=?""",
                    (now, idempotency_key, lease_owner, row["version"]),
                )
                updated = db.execute("SELECT * FROM tool_actions WHERE idempotency_key=?", (idempotency_key,)).fetchone()
                db.commit()
            except Exception:
                db.rollback()
                raise
        return self._record(updated)

    def release_authorization_claim(self, idempotency_key: str, lease_owner: str) -> ToolAction:
        """Return a pre-dispatch claim to prepared when approval validation fails."""
        self._validate_key(idempotency_key)
        if not isinstance(lease_owner, str) or not lease_owner:
            raise ValueError("lease_owner must be a non-empty fencing token.")
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("SELECT * FROM tool_actions WHERE idempotency_key=?", (idempotency_key,)).fetchone()
                if row is None or row["state"] != "authorizing" or row["lease_owner"] != lease_owner:
                    raise ToolActionTransitionError("authorization claim is no longer owned by this worker.")
                db.execute(
                    """UPDATE tool_actions SET state='prepared',updated_at=?,version=version+1,
                       lease_owner=NULL,lease_expires_at=NULL
                       WHERE idempotency_key=? AND state='authorizing' AND lease_owner=? AND version=?""",
                    (now, idempotency_key, lease_owner, row["version"]),
                )
                updated = db.execute("SELECT * FROM tool_actions WHERE idempotency_key=?", (idempotency_key,)).fetchone()
                db.commit()
            except Exception:
                db.rollback()
                raise
        return self._record(updated)

    def begin_execution(
        self, idempotency_key: str, request_digest: str, *, lease_seconds: int = 120,
    ) -> ToolAction:
        """Atomically grant one dispatch attempt and issue its fencing token."""
        self._validate_key(idempotency_key)
        if not isinstance(request_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", request_digest):
            raise ValueError("request_digest must be a SHA-256 hex digest.")
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or not 5 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be an integer from 5 to 3600.")
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        expires = (now_dt + timedelta(seconds=lease_seconds)).isoformat()
        owner = secrets.token_urlsafe(24)
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
                    """UPDATE tool_actions SET state='executing',updated_at=?,version=version+1,
                       lease_owner=?,lease_expires_at=?
                       WHERE idempotency_key=? AND state='prepared' AND version=?""",
                    (now, owner, expires, idempotency_key, row["version"]),
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

    def renew_execution(self, idempotency_key: str, lease_owner: str, *, lease_seconds: int = 120) -> ToolAction:
        """Renew a live dispatch lease; stale workers cannot extend a recovered action."""
        self._validate_key(idempotency_key)
        if not isinstance(lease_owner, str) or not lease_owner:
            raise ValueError("lease_owner must be a non-empty fencing token.")
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or not 5 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be an integer from 5 to 3600.")
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        expires = (now_dt + timedelta(seconds=lease_seconds)).isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    "SELECT * FROM tool_actions WHERE idempotency_key=?", (idempotency_key,),
                ).fetchone()
                if row is None or row["state"] != "executing" or row["lease_owner"] != lease_owner:
                    raise ToolActionTransitionError("execution lease is no longer owned by this worker.")
                if row["lease_expires_at"] <= now:
                    raise ToolActionTransitionError("execution lease has expired.")
                db.execute(
                    """UPDATE tool_actions SET lease_expires_at=?,updated_at=?,version=version+1
                       WHERE idempotency_key=? AND state='executing' AND lease_owner=? AND version=?""",
                    (expires, now, idempotency_key, lease_owner, row["version"]),
                )
                updated = db.execute(
                    "SELECT * FROM tool_actions WHERE idempotency_key=?", (idempotency_key,),
                ).fetchone()
                db.commit()
            except Exception:
                db.rollback()
                raise
        return self._record(updated)

    def recover_expired_execution(self, idempotency_key: str) -> ToolAction | None:
        """Fence one expired execution lease and return its durable current state."""
        self._validate_key(idempotency_key)
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute(
                    """UPDATE tool_actions SET state='prepared',error_code=NULL,
                       lease_owner=NULL,lease_expires_at=NULL,updated_at=?,version=version+1
                       WHERE idempotency_key=? AND state='authorizing'
                         AND lease_expires_at IS NOT NULL AND lease_expires_at<=?""",
                    (now, idempotency_key, now),
                )
                db.execute(
                    """UPDATE tool_actions
                       SET state='outcome_unknown',error_code='execution_lease_expired',
                           lease_owner=NULL,lease_expires_at=NULL,updated_at=?,version=version+1
                       WHERE idempotency_key=? AND state='executing'
                         AND lease_expires_at IS NOT NULL AND lease_expires_at<=?""",
                    (now, idempotency_key, now),
                )
                row = db.execute(
                    "SELECT * FROM tool_actions WHERE idempotency_key=?",
                    (idempotency_key,),
                ).fetchone()
                db.commit()
            except Exception:
                db.rollback()
                raise
        return self._record(row) if row is not None else None

    def recover_expired_executions(self) -> int:
        """Fence expired workers and mark their external outcome as unknown."""
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                authorizing_cursor = db.execute(
                    """UPDATE tool_actions SET state='prepared',error_code=NULL,
                       lease_owner=NULL,lease_expires_at=NULL,updated_at=?,version=version+1
                       WHERE state='authorizing' AND lease_expires_at IS NOT NULL
                         AND lease_expires_at<=?""",
                    (now, now),
                )
                executing_cursor = db.execute(
                    """UPDATE tool_actions
                       SET state='outcome_unknown',error_code='execution_lease_expired',
                           lease_owner=NULL,lease_expires_at=NULL,updated_at=?,version=version+1
                       WHERE state='executing' AND lease_expires_at IS NOT NULL
                         AND lease_expires_at<=?""",
                    (now, now),
                )
                count = authorizing_cursor.rowcount + executing_cursor.rowcount
                db.commit()
            except Exception:
                db.rollback()
                raise
        return count

    def _transition(
        self, idempotency_key: str, expected_state: str, new_state: str,
        *, result: Any = None, error_code: str | None = None, lease_owner: str | None = None,
    ) -> ToolAction:
        self._validate_key(idempotency_key)
        result_json = None
        if new_state == "succeeded" or (new_state == "not_executed" and result is not None):
            result_json, _ = _json_bytes(result, _MAX_RESULT_BYTES, "result/evidence")
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
                if expected_state == "executing":
                    if not isinstance(lease_owner, str) or not lease_owner or row["lease_owner"] != lease_owner:
                        raise ToolActionTransitionError("execution lease is not owned by this worker.")
                    if row["lease_expires_at"] is None or row["lease_expires_at"] <= now:
                        raise ToolActionTransitionError("execution lease has expired.")
                db.execute(
                    """UPDATE tool_actions SET state=?,result_json=?,error_code=?,
                       updated_at=?,version=version+1,lease_owner=NULL,lease_expires_at=NULL
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

    def mark_succeeded(self, idempotency_key: str, lease_owner: str, result: Any) -> ToolAction:
        return self._transition(idempotency_key, "executing", "succeeded", result=result, lease_owner=lease_owner)

    def mark_outcome_unknown(self, idempotency_key: str, lease_owner: str, error_code: str = "dispatch_interrupted") -> ToolAction:
        """Record that a side effect may have happened; this state is never auto-replayed."""
        return self._transition(
            idempotency_key, "executing", "outcome_unknown", error_code=error_code, lease_owner=lease_owner,
        )

    def mark_not_executed(self, idempotency_key: str, lease_owner: str, error_code: str) -> ToolAction:
        """Use only when the adapter can prove no external side effect occurred."""
        return self._transition(
            idempotency_key, "executing", "not_executed", error_code=error_code, lease_owner=lease_owner,
        )

    def reconcile_succeeded(self, idempotency_key: str, result: Any) -> ToolAction:
        """Resolve an uncertain action after authoritative provider reconciliation."""
        return self._transition(
            idempotency_key, "outcome_unknown", "succeeded", result=result,
        )

    def reconcile_not_executed(
        self, idempotency_key: str, error_code: str, *, evidence: Any = None,
    ) -> ToolAction:
        """Resolve an uncertain action only after proving non-execution; persist evidence."""
        return self._transition(
            idempotency_key, "outcome_unknown", "not_executed",
            error_code=error_code, result=evidence,
        )
