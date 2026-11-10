"""Permission-aware, append-only specialist experience records.

Stores bounded evaluation metadata, not prompts or model outputs. Recording an
experience never writes to long-term memory or changes agent/model routing.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
import sqlite3
from threading import RLock
from uuid import uuid4

_ID = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.:@/-]{0,199}$")
_CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")


class AgentExperienceStore:
    def __init__(self, database_path: str, *, authorize):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path_required")
        if not callable(authorize):
            raise TypeError("trusted_authorize_callback_required")
        self.database_path = str(Path(database_path).expanduser())
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self.authorize = authorize
        self._lock = RLock()
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS agent_experiences (
                experience_id TEXT PRIMARY KEY,
                actor_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                outcome TEXT NOT NULL CHECK(outcome IN ('succeeded','failed','cancelled')),
                quality_score REAL,
                duration_ms INTEGER NOT NULL CHECK(duration_ms >= 0),
                memory_useful INTEGER,
                evaluator TEXT NOT NULL,
                evidence_ref TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""")
            db.execute("""CREATE INDEX IF NOT EXISTS idx_agent_experience_agent
                ON agent_experiences(actor_id, agent_id, created_at)""")
            db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_experience_task
                ON agent_experiences(actor_id, task_id)""")

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA synchronous=FULL")
        return _Connection(db)

    def _allowed(self, actor_id, action, agent_id):
        if not isinstance(actor_id, str) or not _ID.fullmatch(actor_id):
            return False
        try:
            return self.authorize(actor_id, action, agent_id) is True
        except Exception:
            return False

    def record(self, *, actor_id, agent_id, task_id, outcome, duration_ms,
               evaluator, evidence_ref, quality_score=None, memory_useful=None):
        if not isinstance(agent_id, str) or not _CODE.fullmatch(agent_id):
            raise ValueError("invalid_agent_id")
        if not isinstance(task_id, str) or not _ID.fullmatch(task_id):
            raise ValueError("invalid_task_id")
        if not isinstance(outcome, str) or outcome not in {"succeeded", "failed", "cancelled"}:
            raise ValueError("invalid_outcome")
        if type(duration_ms) is not int or not 0 <= duration_ms <= 86_400_000:
            raise ValueError("duration_ms_must_be_between_0_and_86400000")
        if not isinstance(evaluator, str) or not evaluator.strip() or len(evaluator) > 100:
            raise ValueError("evaluator_required")
        if not isinstance(evidence_ref, str) or not evidence_ref.strip() or len(evidence_ref) > 500:
            raise ValueError("evidence_ref_required")
        if quality_score is not None and (
            isinstance(quality_score, bool) or not isinstance(quality_score, (int, float))
            or not 0 <= quality_score <= 1
        ):
            raise ValueError("quality_score_must_be_between_0_and_1")
        if memory_useful is not None and type(memory_useful) is not bool:
            raise ValueError("memory_useful_must_be_boolean_or_none")
        if not self._allowed(actor_id, "agent_experience:write", agent_id):
            raise PermissionError("experience recording is not authorized")
        experience_id = uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        try:
            with self._lock, self._connect() as db:
                db.execute("""INSERT INTO agent_experiences
                    (experience_id,actor_id,agent_id,task_id,outcome,quality_score,
                     duration_ms,memory_useful,evaluator,evidence_ref,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (experience_id, actor_id, agent_id, task_id, outcome,
                     float(quality_score) if quality_score is not None else None,
                     duration_ms, int(memory_useful) if memory_useful is not None else None,
                     evaluator.strip(), evidence_ref.strip(), now))
                row = db.execute("SELECT * FROM agent_experiences WHERE experience_id=?",
                                 (experience_id,)).fetchone()
        except sqlite3.IntegrityError as exc:
            raise ValueError("experience_already_recorded_for_task") from exc
        return self._serialize(row)

    @staticmethod
    def _serialize(row):
        item = dict(row)
        item["memory_useful"] = None if item["memory_useful"] is None else bool(item["memory_useful"])
        return item

    def summary(self, *, actor_id, agent_id, limit=100):
        if not isinstance(agent_id, str) or not _CODE.fullmatch(agent_id):
            raise ValueError("invalid_agent_id")
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit_must_be_1_to_1000")
        if not self._allowed(actor_id, "agent_experience:read", agent_id):
            raise PermissionError("experience read is not authorized")
        with self._connect() as db:
            rows = db.execute("""SELECT * FROM agent_experiences
                WHERE actor_id=? AND agent_id=? ORDER BY created_at DESC LIMIT ?""",
                (actor_id, agent_id, limit)).fetchall()
        items = [self._serialize(row) for row in rows]
        scored = [x["quality_score"] for x in items if x["quality_score"] is not None]
        durations = [x["duration_ms"] for x in items]
        return {
            "agent_id": agent_id,
            "sample_count": len(items),
            "success_count": sum(x["outcome"] == "succeeded" for x in items),
            "failure_count": sum(x["outcome"] == "failed" for x in items),
            "cancelled_count": sum(x["outcome"] == "cancelled" for x in items),
            "mean_quality_score": round(sum(scored) / len(scored), 4) if scored else None,
            "mean_duration_ms": round(sum(durations) / len(durations), 2) if durations else None,
            "memory_useful_count": sum(x["memory_useful"] is True for x in items),
            "records": items,
            "routing_changed": False,
            "memory_written": False,
        }


class _Connection:
    def __init__(self, db):
        self.db = db
    def __enter__(self):
        return self.db
    def __exit__(self, exc_type, exc, tb):
        try:
            self.db.commit() if exc_type is None else self.db.rollback()
        finally:
            self.db.close()
        return False
