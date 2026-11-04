"""Explicit promotion of human-approved agent knowledge into durable memory.

The review queue and memory store must share one SQLite database so review
verification, memory insertion, and the promotion ledger commit atomically.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

from core.agent_memory import AgentMemoryStore
from evaluation.agent_review_queue import AgentEvaluationReviewQueue

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,199}$")
_MAX_CONTENT = 8000
_MAX_SOURCE = 500


class ApprovedKnowledgePromoter:
    """Promote a specific approved evaluation into scoped long-term memory.

    This service intentionally does not infer facts from an evaluation report.
    The caller supplies the exact candidate text and provenance; the service
    verifies that the referenced review was approved by the same owner.
    """

    def __init__(self, memory_store: AgentMemoryStore,
                 review_queue: AgentEvaluationReviewQueue):
        if not isinstance(memory_store, AgentMemoryStore):
            raise TypeError("memory_store_must_be_AgentMemoryStore")
        if not isinstance(review_queue, AgentEvaluationReviewQueue):
            raise TypeError("review_queue_must_be_AgentEvaluationReviewQueue")
        if memory_store.database_path != review_queue.database_path:
            raise ValueError("promotion_stores_must_share_database")
        self.memory_store = memory_store
        self.review_queue = review_queue
        self.database_path = memory_store.database_path
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS agent_knowledge_promotions (
                    actor_id TEXT NOT NULL,
                    evaluation_id TEXT NOT NULL,
                    memory_id TEXT,
                    candidate_sha256 TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('promoted','duplicate')),
                    reviewer_actor_id TEXT NOT NULL,
                    promoted_at TEXT NOT NULL,
                    PRIMARY KEY(actor_id, evaluation_id)
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_knowledge_promotion_fingerprint
                ON agent_knowledge_promotions(actor_id, candidate_sha256)
            """)

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        return _Connection(db)

    def promote(self, *, actor_id: str, evaluation_id: str, agent_id: str,
                content: str, source: str, category: str = "semantic",
                scope: str = "private", confidence: float = 0.9,
                retention_days: int = 365) -> dict:
        """Promote one exact candidate; repeated calls are idempotent."""
        if not isinstance(actor_id, str) or not _ID.fullmatch(actor_id):
            raise ValueError("invalid_actor_id")
        if not isinstance(agent_id, str) or not _ID.fullmatch(agent_id):
            raise ValueError("invalid_agent_id")
        if not isinstance(evaluation_id, str) or not re.fullmatch(
            r"eval-[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,199}", evaluation_id
        ):
            raise ValueError("invalid_evaluation_id")
        if not isinstance(content, str) or not content.strip() or len(content) > _MAX_CONTENT or "\x00" in content:
            raise ValueError("invalid_knowledge_content")
        if not isinstance(source, str) or not source.strip() or len(source) > _MAX_SOURCE:
            raise ValueError("invalid_knowledge_source")
        if category not in {"working", "episodic", "semantic", "procedural"}:
            raise ValueError("invalid_memory_category")
        if scope not in {"private", "shared"}:
            raise ValueError("invalid_memory_scope")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            raise ValueError("invalid_memory_confidence")
        if type(retention_days) is not int or not 1 <= retention_days <= 3650:
            raise ValueError("retention_days_must_be_1_to_3650")

        action = "agent_memory:write" if scope == "private" else "agent_memory:share"
        if not self.memory_store._allowed(actor_id, action, agent_id):
            raise PermissionError("knowledge promotion is not authorized")

        normalized = " ".join(content.split()).casefold()
        candidate_digest = hashlib.sha256(
            f"{agent_id}\0{category}\0{scope}\0{normalized}".encode("utf-8")
        ).hexdigest()
        now = datetime.now(timezone.utc)
        memory_id = uuid4().hex
        expires_at = (now + timedelta(days=retention_days)).isoformat()
        source_value = f"evaluation:{evaluation_id}|{source.strip()}"

        # BEGIN IMMEDIATE serializes concurrent promotion attempts. The approved
        # review, duplicate check, memory insert and ledger write are one commit.
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            review = db.execute("""
                SELECT status, reviewer_actor_id, task_id, agent_id, report_json
                FROM agent_evaluation_reviews
                WHERE actor_id=? AND evaluation_id=?
            """, (actor_id, evaluation_id)).fetchone()
            if review is None:
                raise LookupError("evaluation_review_not_found")
            if review["status"] != "approved" or review["reviewer_actor_id"] != actor_id:
                raise PermissionError("evaluation_review_not_approved_by_owner")
            if review["agent_id"] != agent_id:
                raise ValueError("evaluation_agent_mismatch")
            report = json.loads(review["report_json"])
            if not report.get("eligible_for_memory_review", False):
                raise ValueError("evaluation_not_eligible_for_memory_review")
            if report.get("automatic_memory_write", True):
                raise ValueError("invalid_evaluation_memory_policy")

            prior = db.execute("""
                SELECT memory_id, candidate_sha256, status
                FROM agent_knowledge_promotions
                WHERE actor_id=? AND evaluation_id=?
            """, (actor_id, evaluation_id)).fetchone()
            if prior is not None:
                return {
                    "status": prior["status"], "memory_id": prior["memory_id"],
                    "evaluation_id": evaluation_id, "idempotent": True,
                    "memory_written": prior["status"] == "promoted",
                }

            duplicate = db.execute("""
                SELECT memory_id FROM agent_memories
                WHERE actor_id=? AND agent_id=? AND category=? AND scope=?
                  AND lower(trim(content))=lower(trim(?))
                  AND (expires_at IS NULL OR expires_at>?)
                LIMIT 1
            """, (actor_id, agent_id, category, scope, content.strip(), now.isoformat())).fetchone()

            if duplicate is not None:
                db.execute("""
                    INSERT INTO agent_knowledge_promotions
                    (actor_id,evaluation_id,memory_id,candidate_sha256,status,reviewer_actor_id,promoted_at)
                    VALUES(?,?,?,?,?,?,?)
                """, (actor_id, evaluation_id, duplicate["memory_id"], candidate_digest,
                      "duplicate", actor_id, now.isoformat()))
                return {
                    "status": "duplicate", "memory_id": duplicate["memory_id"],
                    "evaluation_id": evaluation_id, "idempotent": False,
                    "memory_written": False,
                }

            db.execute("""
                INSERT INTO agent_memories
                (memory_id,actor_id,agent_id,category,content,source,confidence,
                 verified,scope,created_at,expires_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """, (memory_id, actor_id, agent_id, category, content.strip(),
                  source_value, float(confidence), 1, scope, now.isoformat(), expires_at))
            db.execute("""
                INSERT INTO agent_knowledge_promotions
                (actor_id,evaluation_id,memory_id,candidate_sha256,status,reviewer_actor_id,promoted_at)
                VALUES(?,?,?,?,?,?,?)
            """, (actor_id, evaluation_id, memory_id, candidate_digest,
                  "promoted", actor_id, now.isoformat()))

        return {
            "status": "promoted", "memory_id": memory_id,
            "evaluation_id": evaluation_id, "task_id": review["task_id"],
            "agent_id": agent_id, "scope": scope, "category": category,
            "candidate_sha256": candidate_digest, "memory_written": True,
            "routing_changed": False, "model_updated": False, "idempotent": False,
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
