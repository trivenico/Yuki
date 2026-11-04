"""Explicit promotion of a specifically owner-approved candidate into memory."""
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
_EVALUATION_ID = re.compile(r"eval-[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,199}")
_MAX_CONTENT = 8000
_MAX_SOURCE = 250


class ApprovedKnowledgePromoter:
    """Promote exact owner-approved text and retain auditable revocations."""

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
                CREATE TABLE IF NOT EXISTS agent_knowledge_candidate_reviews (
                    actor_id TEXT NOT NULL, evaluation_id TEXT NOT NULL,
                    candidate_sha256 TEXT NOT NULL, agent_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('approved','rejected')),
                    reviewer_actor_id TEXT NOT NULL, reviewed_at TEXT NOT NULL,
                    review_note TEXT NOT NULL,
                    PRIMARY KEY(actor_id,evaluation_id,candidate_sha256)
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS agent_knowledge_promotions (
                    actor_id TEXT NOT NULL, evaluation_id TEXT NOT NULL,
                    candidate_sha256 TEXT NOT NULL, memory_id TEXT,
                    status TEXT NOT NULL CHECK(status IN ('promoted','duplicate')),
                    reviewer_actor_id TEXT NOT NULL, promoted_at TEXT NOT NULL,
                    PRIMARY KEY(actor_id,evaluation_id,candidate_sha256)
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_knowledge_promotion_fingerprint
                ON agent_knowledge_promotions(actor_id,candidate_sha256)
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS agent_knowledge_revocations (
                    actor_id TEXT NOT NULL, evaluation_id TEXT NOT NULL,
                    candidate_sha256 TEXT NOT NULL, memory_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL, revoked_by TEXT NOT NULL,
                    revoked_at TEXT NOT NULL, reason TEXT NOT NULL,
                    memory_removed INTEGER NOT NULL CHECK(memory_removed IN (0,1)),
                    PRIMARY KEY(actor_id,evaluation_id,candidate_sha256)
                )
            """)

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        return _Connection(db)

    @staticmethod
    def _candidate_digest(*, agent_id, content, source, category, scope,
                          confidence, retention_days):
        normalized = " ".join(content.split()).casefold()
        payload = {
            "agent_id": agent_id, "content": normalized, "source": source.strip(),
            "category": category, "scope": scope, "confidence": float(confidence),
            "retention_days": retention_days,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _validate_candidate(self, *, actor_id, evaluation_id, agent_id, content,
                             source, category, scope, confidence, retention_days):
        if not isinstance(actor_id, str) or not _ID.fullmatch(actor_id):
            raise ValueError("invalid_actor_id")
        if not isinstance(agent_id, str) or not _ID.fullmatch(agent_id):
            raise ValueError("invalid_agent_id")
        if not isinstance(evaluation_id, str) or not _EVALUATION_ID.fullmatch(evaluation_id):
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
        return self._candidate_digest(
            agent_id=agent_id, content=content, source=source, category=category,
            scope=scope, confidence=confidence, retention_days=retention_days,
        )

    def review_candidate(self, *, actor_id: str, evaluation_id: str, agent_id: str,
                         content: str, source: str, decision: str,
                         category: str = "semantic", scope: str = "private",
                         confidence: float = 0.9, retention_days: int = 365,
                         note: str = "") -> dict:
        digest = self._validate_candidate(
            actor_id=actor_id, evaluation_id=evaluation_id, agent_id=agent_id,
            content=content, source=source, category=category, scope=scope,
            confidence=confidence, retention_days=retention_days,
        )
        if decision not in {"approved", "rejected"}:
            raise ValueError("invalid_candidate_decision")
        if not isinstance(note, str) or len(note) > 2000 or "\x00" in note:
            raise ValueError("invalid_review_note")
        if not self.review_queue._allowed(actor_id, "agent_knowledge:review", agent_id):
            raise PermissionError("knowledge candidate review is not authorized")
        action = "agent_memory:write" if scope == "private" else "agent_memory:share"
        if not self.memory_store._allowed(actor_id, action, agent_id):
            raise PermissionError("knowledge candidate scope is not authorized")

        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            review = db.execute("""
                SELECT status,agent_id,report_json FROM agent_evaluation_reviews
                WHERE actor_id=? AND evaluation_id=?
            """, (actor_id, evaluation_id)).fetchone()
            if review is None:
                raise LookupError("evaluation_review_not_found")
            if review["status"] != "approved":
                raise PermissionError("evaluation_review_not_approved_by_owner")
            if review["agent_id"] != agent_id:
                raise ValueError("evaluation_agent_mismatch")
            report = json.loads(review["report_json"])
            if not report.get("eligible_for_memory_review", False):
                raise ValueError("evaluation_not_eligible_for_memory_review")
            db.execute("""
                INSERT OR IGNORE INTO agent_knowledge_candidate_reviews
                (actor_id,evaluation_id,candidate_sha256,agent_id,status,
                 reviewer_actor_id,reviewed_at,review_note)
                VALUES(?,?,?,?,?,?,?,?)
            """, (actor_id,evaluation_id,digest,agent_id,decision,actor_id,
                  datetime.now(timezone.utc).isoformat(),note.strip()))
            row = db.execute("""
                SELECT status,candidate_sha256,reviewer_actor_id
                FROM agent_knowledge_candidate_reviews
                WHERE actor_id=? AND evaluation_id=? AND candidate_sha256=?
            """, (actor_id,evaluation_id,digest)).fetchone()
        return dict(row)

    def promote(self, *, actor_id: str, evaluation_id: str, agent_id: str,
                content: str, source: str, category: str = "semantic",
                scope: str = "private", confidence: float = 0.9,
                retention_days: int = 365) -> dict:
        digest = self._validate_candidate(
            actor_id=actor_id,evaluation_id=evaluation_id,agent_id=agent_id,
            content=content,source=source,category=category,scope=scope,
            confidence=confidence,retention_days=retention_days,
        )
        action = "agent_memory:write" if scope == "private" else "agent_memory:share"
        if not self.memory_store._allowed(actor_id, action, agent_id):
            raise PermissionError("knowledge promotion is not authorized")
        now = datetime.now(timezone.utc)
        memory_id = uuid4().hex
        expires_at = (now + timedelta(days=retention_days)).isoformat()
        source_value = f"evaluation:{evaluation_id}|{source.strip()}"
        if len(source_value) > 500:
            raise ValueError("combined_knowledge_source_too_long")

        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            review = db.execute("""
                SELECT status,reviewer_actor_id,task_id,agent_id,report_json
                FROM agent_evaluation_reviews WHERE actor_id=? AND evaluation_id=?
            """, (actor_id,evaluation_id)).fetchone()
            if review is None:
                raise LookupError("evaluation_review_not_found")
            if review["status"] != "approved" or review["reviewer_actor_id"] != actor_id:
                raise PermissionError("evaluation_review_not_approved_by_owner")
            if review["agent_id"] != agent_id:
                raise ValueError("evaluation_agent_mismatch")
            if not json.loads(review["report_json"]).get("eligible_for_memory_review", False):
                raise ValueError("evaluation_not_eligible_for_memory_review")
            candidate = db.execute("""
                SELECT status,reviewer_actor_id FROM agent_knowledge_candidate_reviews
                WHERE actor_id=? AND evaluation_id=? AND candidate_sha256=? AND agent_id=?
            """, (actor_id,evaluation_id,digest,agent_id)).fetchone()
            if candidate is None or candidate["status"] != "approved" or candidate["reviewer_actor_id"] != actor_id:
                raise PermissionError("exact_knowledge_candidate_not_approved")
            prior = db.execute("""
                SELECT memory_id,status FROM agent_knowledge_promotions
                WHERE actor_id=? AND evaluation_id=? AND candidate_sha256=?
            """, (actor_id,evaluation_id,digest)).fetchone()
            if prior is not None:
                return {"status":prior["status"],"memory_id":prior["memory_id"],
                        "evaluation_id":evaluation_id,"idempotent":True,
                        "memory_written":prior["status"]=="promoted"}
            duplicate = db.execute("""
                SELECT memory_id FROM agent_memories
                WHERE actor_id=? AND agent_id=? AND category=? AND scope=?
                  AND lower(trim(content))=lower(trim(?))
                  AND (expires_at IS NULL OR expires_at>?) LIMIT 1
            """, (actor_id,agent_id,category,scope,content.strip(),now.isoformat())).fetchone()
            status = "duplicate" if duplicate is not None else "promoted"
            final_memory_id = duplicate["memory_id"] if duplicate is not None else memory_id
            if duplicate is None:
                db.execute("""
                    INSERT INTO agent_memories
                    (memory_id,actor_id,agent_id,category,content,source,confidence,
                     verified,scope,created_at,expires_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """, (memory_id,actor_id,agent_id,category,content.strip(),source_value,
                      float(confidence),1,scope,now.isoformat(),expires_at))
            db.execute("""
                INSERT INTO agent_knowledge_promotions
                (actor_id,evaluation_id,candidate_sha256,memory_id,status,reviewer_actor_id,promoted_at)
                VALUES(?,?,?,?,?,?,?)
            """, (actor_id,evaluation_id,digest,final_memory_id,status,actor_id,now.isoformat()))
        return {"status":status,"memory_id":final_memory_id,"evaluation_id":evaluation_id,
                "task_id":review["task_id"],"agent_id":agent_id,"scope":scope,
                "category":category,"candidate_sha256":digest,
                "memory_written":duplicate is None,"routing_changed":False,
                "model_updated":False,"idempotent":False}

    def revoke(self, *, actor_id: str, evaluation_id: str,
               candidate_sha256: str, reason: str) -> dict:
        """Remove a promoted memory while retaining an immutable revocation event."""
        if not isinstance(actor_id, str) or not _ID.fullmatch(actor_id):
            raise ValueError("invalid_actor_id")
        if not isinstance(evaluation_id, str) or not _EVALUATION_ID.fullmatch(evaluation_id):
            raise ValueError("invalid_evaluation_id")
        if not isinstance(candidate_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", candidate_sha256):
            raise ValueError("invalid_candidate_digest")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000 or "\x00" in reason:
            raise ValueError("invalid_revocation_reason")

        if not self.review_queue._allowed(actor_id, "agent_knowledge:revoke", ""):
            raise PermissionError("knowledge revocation is not authorized")
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            promotion = db.execute("""
                SELECT memory_id,status FROM agent_knowledge_promotions
                WHERE actor_id=? AND evaluation_id=? AND candidate_sha256=?
            """, (actor_id,evaluation_id,candidate_sha256)).fetchone()
            if promotion is None:
                raise LookupError("knowledge_promotion_not_found")
            if promotion["status"] != "promoted" or not promotion["memory_id"]:
                raise ValueError("duplicate_promotion_does_not_own_memory")
            prior = db.execute("""
                SELECT * FROM agent_knowledge_revocations
                WHERE actor_id=? AND evaluation_id=? AND candidate_sha256=?
            """, (actor_id,evaluation_id,candidate_sha256)).fetchone()
            if prior is not None:
                return {"status":"revoked","memory_id":prior["memory_id"],
                        "memory_removed":bool(prior["memory_removed"]),"idempotent":True}
            memory = db.execute("""
                SELECT agent_id FROM agent_memories
                WHERE actor_id=? AND memory_id=?
            """, (actor_id,promotion["memory_id"])).fetchone()
            if memory is None:
                raise LookupError("promoted_memory_not_found")
            agent_id = memory["agent_id"]
            if not self.memory_store._allowed(actor_id, "agent_memory:delete", agent_id):
                raise PermissionError("memory deletion is not authorized")
            cursor = db.execute("""
                DELETE FROM agent_memories WHERE actor_id=? AND memory_id=? AND agent_id=?
            """, (actor_id,promotion["memory_id"],agent_id))
            if cursor.rowcount != 1:
                raise RuntimeError("promoted_memory_delete_race")
            self.memory_store._delete_embedding_cache_rows(
                db, actor_id=actor_id, memory_ids=[promotion["memory_id"]]
            )
            db.execute("""
                INSERT INTO agent_knowledge_revocations
                (actor_id,evaluation_id,candidate_sha256,memory_id,agent_id,
                 revoked_by,revoked_at,reason,memory_removed)
                VALUES(?,?,?,?,?,?,?,?,1)
            """, (actor_id,evaluation_id,candidate_sha256,promotion["memory_id"],
                  agent_id,actor_id,now,reason.strip()))
        return {"status":"revoked","memory_id":promotion["memory_id"],
                "agent_id":agent_id,"memory_removed":True,"revoked_at":now,
                "reason":reason.strip(),"idempotent":False}

    def get_revocation(self, *, actor_id: str, evaluation_id: str,
                       candidate_sha256: str) -> dict:
        if not self.review_queue._allowed(actor_id, "agent_knowledge:read", ""):
            raise PermissionError("knowledge audit read is not authorized")
        with self._connect() as db:
            row = db.execute("""
                SELECT * FROM agent_knowledge_revocations
                WHERE actor_id=? AND evaluation_id=? AND candidate_sha256=?
            """, (actor_id,evaluation_id,candidate_sha256)).fetchone()
        if row is None:
            raise LookupError("knowledge_revocation_not_found")
        result = dict(row)
        result["memory_removed"] = bool(result["memory_removed"])
        return result


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
