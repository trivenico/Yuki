"""Durable, authorization-gated recovery incident and attempt journal."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sqlite3
from typing import Callable

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class ReviewerRecoveryIncidentError(RuntimeError):
    pass


class ReviewerRecoveryIncidentConflict(ReviewerRecoveryIncidentError):
    pass


@dataclass(frozen=True)
class RecoveryIncident:
    task_id: str
    worker_id: str
    state: str
    attempts: int
    max_attempts: int
    next_attempt_at: str | None
    last_reason_code: str
    updated_at: str


class ReviewerRecoveryIncidentStore:
    """Stores operational metadata only; never accepts credentials or lease tokens."""

    def __init__(
        self, database: str | Path, *,
        authorize: Callable[[str, str, str], bool],
        default_max_attempts: int = 5,
        default_cooldown_seconds: int = 300,
    ) -> None:
        if not callable(authorize):
            raise TypeError("recovery_incident_authorizer_required")
        if type(default_max_attempts) is not int or not 1 <= default_max_attempts <= 100:
            raise ValueError("invalid_recovery_incident_max_attempts")
        if type(default_cooldown_seconds) is not int or not 1 <= default_cooldown_seconds <= 86400:
            raise ValueError("invalid_recovery_incident_cooldown")
        self.database = str(database)
        self.authorize = authorize
        self.default_max_attempts = default_max_attempts
        self.default_cooldown_seconds = default_cooldown_seconds
        Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS reviewer_recovery_incidents (
                task_id TEXT PRIMARY KEY,
                worker_id TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('open','cooldown','recovered','dead_letter')),
                attempts INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL,
                next_attempt_at TEXT,
                last_reason_code TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS reviewer_recovery_incident_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL REFERENCES reviewer_recovery_incidents(task_id),
                event_type TEXT NOT NULL,
                reason_code TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                metadata_json TEXT NOT NULL
            )""")

    def _connect(self):
        db = sqlite3.connect(self.database, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        return db

    @staticmethod
    def _id(value: str, code: str) -> str:
        if not isinstance(value, str) or not _ID.fullmatch(value):
            raise ValueError(code)
        return value

    def _allowed(self, actor: str, action: str, task: str) -> None:
        if not self.authorize(actor, action, task):
            raise PermissionError("reviewer recovery incident access denied")

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _stamp(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds")

    def begin_attempt(
        self, *, actor_id: str, task_id: str, worker_id: str,
        max_attempts: int | None = None, cooldown_seconds: int | None = None,
    ) -> bool:
        actor = self._id(actor_id, "invalid_recovery_incident_actor")
        task = self._id(task_id, "invalid_recovery_incident_task")
        worker = self._id(worker_id, "invalid_recovery_incident_worker")
        maximum = self.default_max_attempts if max_attempts is None else max_attempts
        cooldown = self.default_cooldown_seconds if cooldown_seconds is None else cooldown_seconds
        if type(maximum) is not int or not 1 <= maximum <= 100:
            raise ValueError("invalid_recovery_incident_max_attempts")
        if type(cooldown) is not int or not 1 <= cooldown <= 86400:
            raise ValueError("invalid_recovery_incident_cooldown")
        self._allowed(actor, "review:recovery:attempt", task)
        now = self._now()
        stamp = self._stamp(now)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM reviewer_recovery_incidents WHERE task_id=?", (task,)
            ).fetchone()
            if row is None:
                db.execute("""INSERT INTO reviewer_recovery_incidents
                    (task_id,worker_id,state,attempts,max_attempts,next_attempt_at,
                     last_reason_code,created_at,updated_at)
                    VALUES (?,?,'open',0,?,NULL,'recovery_candidate',?,?)""",
                    (task, worker, maximum, stamp, stamp))
                attempts = 0
                next_at = None
            else:
                if row["worker_id"] != worker:
                    # A task can move to a new reviewer after reassignment; preserve
                    # one task incident and update the current responsible identity.
                    db.execute("UPDATE reviewer_recovery_incidents SET worker_id=?,updated_at=? WHERE task_id=?",
                               (worker, stamp, task))
                attempts = row["attempts"]
                next_at = row["next_attempt_at"]
                if row["state"] in ("recovered", "dead_letter") or attempts >= row["max_attempts"]:
                    return False
                if next_at and datetime.fromisoformat(next_at) > now:
                    return False
            db.execute("""UPDATE reviewer_recovery_incidents SET state='open',
                attempts=attempts+1,next_attempt_at=NULL,updated_at=? WHERE task_id=?""",
                (stamp, task))
            db.execute("""INSERT INTO reviewer_recovery_incident_events
                (task_id,event_type,reason_code,occurred_at,metadata_json)
                VALUES (?,'attempt_started','recovery_candidate',?,'{}')""",
                (task, stamp))
            return True

    def record_outcome(
        self, *, actor_id: str, task_id: str, state: str, reason_code: str,
        cooldown_seconds: int | None = None,
    ) -> RecoveryIncident:
        actor = self._id(actor_id, "invalid_recovery_incident_actor")
        task = self._id(task_id, "invalid_recovery_incident_task")
        if state not in ("recovered", "cooldown", "dead_letter", "open"):
            raise ValueError("invalid_recovery_incident_state")
        if not isinstance(reason_code, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,95}", reason_code):
            raise ValueError("invalid_recovery_incident_reason")
        cooldown = self.default_cooldown_seconds if cooldown_seconds is None else cooldown_seconds
        if type(cooldown) is not int or not 1 <= cooldown <= 86400:
            raise ValueError("invalid_recovery_incident_cooldown")
        self._allowed(actor, "review:recovery:record", task)
        now = self._now()
        stamp = self._stamp(now)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM reviewer_recovery_incidents WHERE task_id=?", (task,)).fetchone()
            if row is None:
                raise KeyError("recovery incident not found")
            state = "dead_letter" if row["attempts"] >= row["max_attempts"] and state != "recovered" else state
            next_at = self._stamp(now + timedelta(seconds=cooldown)) if state == "cooldown" else None
            db.execute("""UPDATE reviewer_recovery_incidents SET state=?,next_attempt_at=?,
                last_reason_code=?,updated_at=? WHERE task_id=?""",
                (state, next_at, reason_code, stamp, task))
            db.execute("""INSERT INTO reviewer_recovery_incident_events
                (task_id,event_type,reason_code,occurred_at,metadata_json)
                VALUES (?,?,?,?, '{}')""", (task, state, reason_code, stamp))
            updated = db.execute("SELECT * FROM reviewer_recovery_incidents WHERE task_id=?", (task,)).fetchone()
            return RecoveryIncident(
                updated["task_id"], updated["worker_id"], updated["state"],
                updated["attempts"], updated["max_attempts"], updated["next_attempt_at"],
                updated["last_reason_code"], updated["updated_at"],
            )

    def get(self, *, actor_id: str, task_id: str) -> RecoveryIncident:
        actor = self._id(actor_id, "invalid_recovery_incident_actor")
        task = self._id(task_id, "invalid_recovery_incident_task")
        self._allowed(actor, "review:recovery:inspect", task)
        with self._connect() as db:
            row = db.execute("SELECT * FROM reviewer_recovery_incidents WHERE task_id=?", (task,)).fetchone()
            if row is None:
                raise KeyError("recovery incident not found")
        return RecoveryIncident(
            row["task_id"], row["worker_id"], row["state"], row["attempts"],
            row["max_attempts"], row["next_attempt_at"], row["last_reason_code"],
            row["updated_at"],
        )

    def list_events(self, *, actor_id: str, task_id: str) -> tuple[dict, ...]:
        actor = self._id(actor_id, "invalid_recovery_incident_actor")
        task = self._id(task_id, "invalid_recovery_incident_task")
        self._allowed(actor, "review:recovery:inspect", task)
        with self._connect() as db:
            rows = db.execute("""SELECT event_type,reason_code,occurred_at,metadata_json
                FROM reviewer_recovery_incident_events WHERE task_id=? ORDER BY event_id""",
                (task,)).fetchall()
        return tuple({**dict(row), "metadata": json.loads(row["metadata_json"])} for row in rows)


__all__ = [
    "ReviewerRecoveryIncidentStore", "ReviewerRecoveryIncidentError",
    "ReviewerRecoveryIncidentConflict", "RecoveryIncident",
]
