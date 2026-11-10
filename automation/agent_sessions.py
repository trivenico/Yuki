"""Durable, actor-scoped conversation sessions for Yuki agents.

This module stores conversation events and session metadata only. It does not
execute tools, resume workflows, or replay external side effects.
"""
from __future__ import annotations
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
from typing import Any, Callable

class AgentSessionError(RuntimeError):
    """Base error for agent session persistence."""

class SessionConflict(AgentSessionError):
    """Raised for idempotency conflicts or stale session versions."""

_MAX_ID = 200
_MAX_EVENT_KIND = 80
_MAX_PAYLOAD_BYTES = 64 * 1024
_MAX_EVENTS_PAGE = 500
_STATES = frozenset({"active", "waiting_approval", "paused", "completed", "failed", "archived"})
_TERMINAL = frozenset({"completed", "failed", "archived"})

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _text(value: str, field: str, limit: int = _MAX_ID) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
        raise ValueError(f"{field} must be non-empty text of at most {limit} characters.")
    return value.strip()

def _json(value: Any, limit: int = _MAX_PAYLOAD_BYTES) -> str:
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError("event payload must be JSON-serializable.") from exc
    if len(encoded.encode("utf-8")) > limit:
        raise ValueError("event payload exceeds the configured size limit.")
    return encoded

@dataclass(frozen=True)
class AgentSession:
    session_id: str
    actor_id: str
    title: str
    agent_id: str
    model_id: str | None
    workspace_id: str | None
    state: str
    created_at: str
    updated_at: str
    version: int
    last_sequence: int

@dataclass(frozen=True)
class SessionEvent:
    session_id: str
    sequence: int
    event_id: str
    kind: str
    actor_id: str
    payload: dict[str, Any]
    created_at: str

class AgentSessionStore:
    """SQLite-backed append-only session event log with actor authorization."""

    def __init__(self, database_path: str | Path, *, authorize: Callable[[str, str, str], bool]):
        if not callable(authorize):
            raise ValueError("authorize callback is required.")
        self.database_path = str(Path(database_path).expanduser())
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self.authorize = authorize
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("""CREATE TABLE IF NOT EXISTS agent_sessions (
                session_id TEXT PRIMARY KEY, actor_id TEXT NOT NULL, create_key TEXT NOT NULL,
                create_digest TEXT NOT NULL, title TEXT NOT NULL, agent_id TEXT NOT NULL,
                model_id TEXT, workspace_id TEXT, state TEXT NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 0,
                last_sequence INTEGER NOT NULL DEFAULT 0, UNIQUE(actor_id, create_key))""")
            db.execute("""CREATE TABLE IF NOT EXISTS agent_session_events (
                session_id TEXT NOT NULL, sequence INTEGER NOT NULL, event_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL, kind TEXT NOT NULL, actor_id TEXT NOT NULL,
                payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(session_id, sequence), UNIQUE(session_id, idempotency_key),
                FOREIGN KEY(session_id) REFERENCES agent_sessions(session_id) ON DELETE CASCADE)""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_agent_sessions_actor_updated ON agent_sessions(actor_id, updated_at)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_agent_session_events_cursor ON agent_session_events(session_id, sequence)")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=5000")
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA synchronous=FULL")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _authorize(self, actor_id: str, action: str, session_id: str = "*") -> None:
        actor = _text(actor_id, "actor_id")
        try:
            allowed = self.authorize(actor, action, session_id) is True
        except Exception:
            allowed = False
        if not allowed:
            raise PermissionError("agent session authorization denied.")

    @staticmethod
    def _row(row: sqlite3.Row) -> AgentSession:
        return AgentSession(row["session_id"], row["actor_id"], row["title"], row["agent_id"],
            row["model_id"], row["workspace_id"], row["state"], row["created_at"],
            row["updated_at"], row["version"], row["last_sequence"])

    @staticmethod
    def _event(row: sqlite3.Row) -> SessionEvent:
        return SessionEvent(row["session_id"], row["sequence"], row["event_id"], row["kind"],
            row["actor_id"], json.loads(row["payload_json"]), row["created_at"])

    def create(self, *, actor_id: str, idempotency_key: str, title: str,
               agent_id: str = "general", model_id: str | None = None,
               workspace_id: str | None = None) -> AgentSession:
        actor = _text(actor_id, "actor_id")
        key = _text(idempotency_key, "idempotency_key")
        title = _text(title, "title", 300)
        agent = _text(agent_id, "agent_id", 120)
        model = _text(model_id, "model_id", 200) if model_id is not None else None
        workspace = _text(workspace_id, "workspace_id", 300) if workspace_id is not None else None
        self._authorize(actor, "session:create")
        digest = hashlib.sha256(_json([title, agent, model, workspace]).encode()).hexdigest()
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM agent_sessions WHERE actor_id=? AND create_key=?", (actor, key)).fetchone()
            if row:
                if row["create_digest"] != digest:
                    raise SessionConflict("session idempotency key was reused with different metadata.")
                return self._row(row)
            sid = "ys_" + secrets.token_urlsafe(18)
            db.execute("""INSERT INTO agent_sessions
                (session_id,actor_id,create_key,create_digest,title,agent_id,model_id,workspace_id,state,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (sid, actor, key, digest, title, agent, model, workspace, "active", now, now))
            db.execute("""INSERT INTO agent_session_events
                (session_id,sequence,event_id,idempotency_key,kind,actor_id,payload_json,created_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                (sid, 1, "evt_" + secrets.token_hex(12), "session-created", "session.created", actor,
                 _json({"title": title, "agent_id": agent, "model_id": model, "workspace_id": workspace}), now))
            db.execute("UPDATE agent_sessions SET last_sequence=1 WHERE session_id=?", (sid,))
            return self._row(db.execute("SELECT * FROM agent_sessions WHERE session_id=?", (sid,)).fetchone())

    def list_sessions(
        self, *, actor_id: str, before: tuple[str, str] | None = None, limit: int = 50,
    ) -> list[AgentSession]:
        """List the actor's sessions newest-first using a stable keyset cursor."""
        actor = _text(actor_id, "actor_id")
        if not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200.")
        if before is not None:
            if not isinstance(before, tuple) or len(before) != 2:
                raise ValueError("before must be an (updated_at, session_id) tuple.")
            before = (_text(before[0], "cursor.updated_at"), _text(before[1], "cursor.session_id"))
        self._authorize(actor, "session:list")
        with self._connect() as db:
            if before is None:
                rows = db.execute(
                    "SELECT * FROM agent_sessions WHERE actor_id=? "
                    "ORDER BY updated_at DESC, session_id DESC LIMIT ?",
                    (actor, limit),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM agent_sessions WHERE actor_id=? "
                    "AND (updated_at < ? OR (updated_at = ? AND session_id < ?)) "
                    "ORDER BY updated_at DESC, session_id DESC LIMIT ?",
                    (actor, before[0], before[0], before[1], limit),
                ).fetchall()
        return [self._row(row) for row in rows]

    def get(self, *, actor_id: str, session_id: str) -> AgentSession:
        actor, sid = _text(actor_id, "actor_id"), _text(session_id, "session_id")
        self._authorize(actor, "session:read", sid)
        with self._connect() as db:
            row = db.execute("SELECT * FROM agent_sessions WHERE session_id=? AND actor_id=?", (sid, actor)).fetchone()
        if row is None:
            raise KeyError("agent session not found.")
        return self._row(row)

    def append(self, *, actor_id: str, session_id: str, idempotency_key: str,
               kind: str, payload: dict[str, Any], expected_version: int | None = None) -> SessionEvent:
        actor, sid = _text(actor_id, "actor_id"), _text(session_id, "session_id")
        key, event_kind = _text(idempotency_key, "idempotency_key"), _text(kind, "kind", _MAX_EVENT_KIND)
        if not isinstance(payload, dict):
            raise ValueError("payload must be a JSON object.")
        payload_json = _json(payload)
        self._authorize(actor, "session:append", sid)
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            session = db.execute("SELECT * FROM agent_sessions WHERE session_id=? AND actor_id=?", (sid, actor)).fetchone()
            if session is None:
                raise KeyError("agent session not found.")
            existing = db.execute("SELECT * FROM agent_session_events WHERE session_id=? AND idempotency_key=?", (sid, key)).fetchone()
            if existing:
                if existing["kind"] != event_kind or existing["payload_json"] != payload_json:
                    raise SessionConflict("event idempotency key was reused with a different payload.")
                return self._event(existing)
            if session["state"] in _TERMINAL:
                raise SessionConflict("cannot append events to a terminal session.")
            if expected_version is not None and expected_version != session["version"]:
                raise SessionConflict("session version is stale.")
            sequence = session["last_sequence"] + 1
            event_id = "evt_" + secrets.token_hex(12)
            db.execute("""INSERT INTO agent_session_events
                (session_id,sequence,event_id,idempotency_key,kind,actor_id,payload_json,created_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                (sid, sequence, event_id, key, event_kind, actor, payload_json, now))
            db.execute("UPDATE agent_sessions SET last_sequence=?,version=version+1,updated_at=? WHERE session_id=? AND version=?",
                       (sequence, now, sid, session["version"]))
            row = db.execute("SELECT * FROM agent_session_events WHERE session_id=? AND sequence=?", (sid, sequence)).fetchone()
            return self._event(row)

    def list_events(self, *, actor_id: str, session_id: str, after_sequence: int = 0,
                    limit: int = 100) -> list[SessionEvent]:
        actor, sid = _text(actor_id, "actor_id"), _text(session_id, "session_id")
        if not isinstance(after_sequence, int) or after_sequence < 0:
            raise ValueError("after_sequence must be a non-negative integer.")
        if not isinstance(limit, int) or not 1 <= limit <= _MAX_EVENTS_PAGE:
            raise ValueError(f"limit must be between 1 and {_MAX_EVENTS_PAGE}.")
        self._authorize(actor, "session:read", sid)
        with self._connect() as db:
            if db.execute("SELECT 1 FROM agent_sessions WHERE session_id=? AND actor_id=?", (sid, actor)).fetchone() is None:
                raise KeyError("agent session not found.")
            rows = db.execute("SELECT * FROM agent_session_events WHERE session_id=? AND sequence>? ORDER BY sequence LIMIT ?",
                              (sid, after_sequence, limit)).fetchall()
        return [self._event(row) for row in rows]

    def set_state(self, *, actor_id: str, session_id: str, state: str,
                  expected_version: int) -> AgentSession:
        actor, sid, target = _text(actor_id, "actor_id"), _text(session_id, "session_id"), _text(state, "state", 40)
        if target not in _STATES:
            raise ValueError("unsupported session state.")
        if not isinstance(expected_version, int) or expected_version < 0:
            raise ValueError("expected_version must be a non-negative integer.")
        self._authorize(actor, "session:state", sid)
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM agent_sessions WHERE session_id=? AND actor_id=?", (sid, actor)).fetchone()
            if row is None:
                raise KeyError("agent session not found.")
            if row["version"] != expected_version:
                raise SessionConflict("session version is stale.")
            if row["state"] in _TERMINAL and row["state"] != target:
                raise SessionConflict("terminal session state cannot be changed.")
            sequence = row["last_sequence"] + 1
            db.execute("UPDATE agent_sessions SET state=?,version=version+1,last_sequence=?,updated_at=? WHERE session_id=? AND version=?",
                       (target, sequence, now, sid, expected_version))
            db.execute("""INSERT INTO agent_session_events
                (session_id,sequence,event_id,idempotency_key,kind,actor_id,payload_json,created_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                (sid, sequence, "evt_" + secrets.token_hex(12), "state-" + str(expected_version + 1),
                 "session.state_changed", actor, _json({"from": row["state"], "to": target}), now))
            return self._row(db.execute("SELECT * FROM agent_sessions WHERE session_id=?", (sid,)).fetchone())
