"""Owner-scoped human review queue for specialist-agent evaluations.

A review decision records approval of the evaluation report only. Knowledge
promotion is a separate, explicitly authorized operation.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from threading import RLock

from evaluation.agent_evaluator import EvaluationReport

_ACTOR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,199}$")
_NOTE_MAX = 2000


class AgentEvaluationReviewQueue:
    """Persist evaluation reports that require review."""

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
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_evaluation_reviews (
                    actor_id TEXT NOT NULL,
                    evaluation_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    report_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected')),
                    created_at TEXT NOT NULL,
                    reviewer_actor_id TEXT,
                    reviewed_at TEXT,
                    review_note TEXT,
                    PRIMARY KEY(actor_id, evaluation_id)
                )
                """
            )
            db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_agent_evaluation_review_pending
                ON agent_evaluation_reviews(actor_id, status, created_at)
                """
            )

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA synchronous=FULL")
        return _Connection(db)

    def _allowed(self, actor_id: str, action: str, agent_id: str) -> bool:
        if not isinstance(actor_id, str) or not _ACTOR.fullmatch(actor_id):
            return False
        try:
            return self.authorize(actor_id, action, agent_id) is True
        except Exception:
            return False

    def enqueue(self, *, actor_id: str, report: EvaluationReport) -> dict:
        """Queue reports requiring review; duplicate enqueue is idempotent."""
        if not isinstance(report, EvaluationReport):
            raise TypeError("report must be an EvaluationReport")
        if not (report.requires_human_review or report.eligible_for_memory_review):
            raise ValueError("evaluation_does_not_require_review")
        if not self._allowed(actor_id, "agent_evaluation:enqueue", report.agent_id):
            raise PermissionError("evaluation enqueue is not authorized")

        report_json = json.dumps(
            report.to_dict(), ensure_ascii=True, sort_keys=True, separators=(",", ":")
        )
        created_at = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as db:
            db.execute(
                """
                INSERT OR IGNORE INTO agent_evaluation_reviews
                (actor_id,evaluation_id,task_id,agent_id,report_json,status,created_at)
                VALUES(?,?,?,?,?,'pending',?)
                """,
                (actor_id, report.evaluation_id, report.task_id, report.agent_id,
                 report_json, created_at),
            )
            row = db.execute(
                """
                SELECT * FROM agent_evaluation_reviews
                WHERE actor_id=? AND evaluation_id=?
                """,
                (actor_id, report.evaluation_id),
            ).fetchone()
        return self._serialize(row)

    def list_pending(self, *, actor_id: str, limit: int = 50) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit_must_be_1_to_100")
        if not self._allowed(actor_id, "agent_evaluation:read", ""):
            raise PermissionError("evaluation review read is not authorized")
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT * FROM agent_evaluation_reviews
                WHERE actor_id=? AND status='pending'
                ORDER BY created_at ASC LIMIT ?
                """,
                (actor_id, limit),
            ).fetchall()
        return [self._serialize(row) for row in rows]

    def get_review(self, *, actor_id: str, evaluation_id: str) -> dict:
        """Return one owner-scoped review record, including its decision."""
        if not isinstance(evaluation_id, str) or not re.fullmatch(
            r"eval-[A-Za-z0-9_-][A-Za-z0-9_.:@/-]{0,199}", evaluation_id
        ):
            raise ValueError("invalid_evaluation_id")
        if not self._allowed(actor_id, "agent_evaluation:read", ""):
            raise PermissionError("evaluation review read is not authorized")
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM agent_evaluation_reviews WHERE actor_id=? AND evaluation_id=?",
                (actor_id, evaluation_id),
            ).fetchone()
        if row is None:
            raise LookupError("evaluation_review_not_found")
        return self._serialize(row)

    def decide(
        self, *, reviewer_actor_id: str, evaluation_id: str,
        decision: str, note: str = "",
    ) -> dict:
        """Approve or reject one pending report; never mutate memory or routing."""
        if not isinstance(evaluation_id, str) or not re.fullmatch(
            r"eval-[A-Za-z0-9_-][A-Za-z0-9_.:@/-]{0,199}", evaluation_id
        ):
            raise ValueError("invalid_evaluation_id")
        if decision not in {"approved", "rejected"}:
            raise ValueError("decision_must_be_approved_or_rejected")
        if not isinstance(note, str) or len(note) > _NOTE_MAX or "\x00" in note:
            raise ValueError("invalid_review_note")
        if not self._allowed(reviewer_actor_id, "agent_evaluation:review", ""):
            raise PermissionError("evaluation review is not authorized")

        reviewed_at = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as db:
            row = db.execute(
                """
                SELECT * FROM agent_evaluation_reviews
                WHERE actor_id=? AND evaluation_id=?
                """,
                (reviewer_actor_id, evaluation_id),
            ).fetchone()
            if row is None:
                raise LookupError("evaluation_review_not_found")
            if row["status"] != "pending":
                raise ValueError("evaluation_review_already_decided")
            db.execute(
                """
                UPDATE agent_evaluation_reviews
                SET status=?, reviewer_actor_id=?, reviewed_at=?, review_note=?
                WHERE actor_id=? AND evaluation_id=? AND status='pending'
                """,
                (decision, reviewer_actor_id, reviewed_at, note.strip(),
                 reviewer_actor_id, evaluation_id),
            )
            updated = db.execute(
                """
                SELECT * FROM agent_evaluation_reviews
                WHERE actor_id=? AND evaluation_id=?
                """,
                (reviewer_actor_id, evaluation_id),
            ).fetchone()
        return self._serialize(updated)

    @staticmethod
    def _serialize(row) -> dict:
        item = dict(row)
        item["report"] = json.loads(item.pop("report_json"))
        item["memory_written"] = False
        item["routing_changed"] = False
        return item


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
