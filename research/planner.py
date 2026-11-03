"""Bounded research planning for Yuki.

Plans are durable proposals. Creating or enqueueing a plan never executes research,
fetches external sources, mutates knowledge, or bypasses task approval.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from uuid import uuid4


MAX_GOAL_CHARS = 1000
MAX_QUESTIONS = 8
MAX_QUESTION_CHARS = 500
MAX_STEPS = 7


class ResearchPlanner:
    """Create auditable research plans and optionally submit approval-gated tasks."""

    def __init__(self, database_path="YukiData/research-plans.sqlite3", *, task_store=None):
        self.database_path = Path(database_path).expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.task_store = task_store
        self._lock = RLock()
        self._initialize()

    def _connect(self):
        db = sqlite3.connect(str(self.database_path), timeout=10.0, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        return db

    @contextmanager
    def _session(self):
        db = self._connect()
        try:
            yield db
        finally:
            db.close()

    def _initialize(self):
        with self._lock, self._session() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS research_plans (
                    plan_id TEXT PRIMARY KEY,
                    goal TEXT NOT NULL,
                    questions_json TEXT NOT NULL,
                    steps_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN
                        ('draft','queued_for_approval','cancelled')),
                    task_id TEXT UNIQUE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

    @staticmethod
    def _validate_text(value, label, maximum):
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
            raise ValueError(f"{label} must be non-empty text of at most {maximum} characters.")
        return value.strip()

    @classmethod
    def _normalize_questions(cls, questions):
        if questions is None:
            return []
        if not isinstance(questions, (list, tuple)) or len(questions) > MAX_QUESTIONS:
            raise ValueError(f"questions must contain at most {MAX_QUESTIONS} items.")
        result, seen = [], set()
        for question in questions:
            value = cls._validate_text(question, "Each research question", MAX_QUESTION_CHARS)
            key = value.casefold()
            if key not in seen:
                result.append(value)
                seen.add(key)
        return result

    @staticmethod
    def _build_steps(goal, questions):
        scope = questions or [goal]
        return [
            {
                "step_id": "scope",
                "kind": "define_scope",
                "description": "Clarify the research objective, boundaries and success criteria.",
                "depends_on": [],
                "inputs": {"goal": goal, "questions": scope},
                "side_effects": [],
            },
            {
                "step_id": "local_retrieval",
                "kind": "retrieve_local_knowledge",
                "description": "Search Yuki's approved local knowledge sources and retain source attribution.",
                "depends_on": ["scope"],
                "inputs": {"queries": scope},
                "side_effects": [],
            },
            {
                "step_id": "gap_analysis",
                "kind": "identify_knowledge_gaps",
                "description": "Identify unanswered questions and distinguish missing evidence from negative evidence.",
                "depends_on": ["local_retrieval"],
                "inputs": {"questions": scope},
                "side_effects": [],
            },
            {
                "step_id": "source_discovery",
                "kind": "discover_additional_sources",
                "description": "Find candidate sources only through explicitly configured and authorized connectors.",
                "depends_on": ["gap_analysis"],
                "inputs": {"questions": scope, "requires_source_policy": True},
                "side_effects": ["external_network_read"],
            },
            {
                "step_id": "evidence_analysis",
                "kind": "extract_and_compare_evidence",
                "description": "Extract candidate claims, entities and relationships; record supporting, refuting and contextual evidence.",
                "depends_on": ["local_retrieval", "source_discovery"],
                "inputs": {"questions": scope},
                "side_effects": ["knowledge_write_requires_review"],
            },
            {
                "step_id": "synthesis",
                "kind": "synthesize_findings",
                "description": "Separate verified findings, supported claims, contested claims and unresolved questions.",
                "depends_on": ["evidence_analysis"],
                "inputs": {"goal": goal},
                "side_effects": [],
            },
            {
                "step_id": "report",
                "kind": "produce_research_report",
                "description": "Produce a report with source references, uncertainty and explicit limitations.",
                "depends_on": ["synthesis"],
                "inputs": {"goal": goal},
                "side_effects": ["report_write"],
            },
        ]

    def create_plan(self, goal, *, questions=None):
        goal = self._validate_text(goal, "goal", MAX_GOAL_CHARS)
        normalized_questions = self._normalize_questions(questions)
        steps = self._build_steps(goal, normalized_questions)
        now = datetime.now(timezone.utc).isoformat()
        plan_id = uuid4().hex
        with self._lock, self._session() as db:
            db.execute(
                """INSERT INTO research_plans
                   (plan_id, goal, questions_json, steps_json, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'draft', ?, ?)""",
                (plan_id, goal, json.dumps(normalized_questions, ensure_ascii=False),
                 json.dumps(steps, ensure_ascii=False), now, now),
            )
        return self.get_plan(plan_id)

    def enqueue_for_approval(self, plan_id):
        """Submit one approval-gated task envelope; a worker must still enforce policy."""
        if self.task_store is None:
            raise RuntimeError("A configured TaskStore is required to enqueue research.")
        with self._lock, self._session() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM research_plans WHERE plan_id=?", (plan_id,)
            ).fetchone()
            if row is None:
                raise KeyError("Research plan not found.")
            if row["status"] == "cancelled":
                raise ValueError("Cancelled research plans cannot be enqueued.")
            payload = {
                "plan_id": row["plan_id"],
                "goal": row["goal"],
                "questions": json.loads(row["questions_json"]),
                "steps": json.loads(row["steps_json"]),
                "execution_contract": {
                    "mode": "approval_gated",
                    "external_network": "authorized_connectors_only",
                    "knowledge_writes": "evidence_ledger_review_required",
                    "graph_writes": "verified_claims_only",
                    "autonomous_execution": False,
                },
            }
            task = self.task_store.submit(
                "research.investigate",
                payload,
                idempotency_key=f"yuki-research-plan:{plan_id}",
                max_attempts=3,
                budget_tokens=None,
                budget_seconds=None,
                approval_required=True,
            )
            now = datetime.now(timezone.utc).isoformat()
            db.execute(
                """UPDATE research_plans SET status='queued_for_approval',
                   task_id=?, updated_at=? WHERE plan_id=?""",
                (task["id"], now, plan_id),
            )
            db.commit()
        return self.get_plan(plan_id)

    def cancel_plan(self, plan_id):
        with self._lock, self._session() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status FROM research_plans WHERE plan_id=?", (plan_id,)
            ).fetchone()
            if row is None:
                raise KeyError("Research plan not found.")
            if row["status"] == "queued_for_approval":
                raise ValueError("Cancel the queued TaskStore task through its authorized cancellation API.")
            db.execute(
                """UPDATE research_plans SET status='cancelled',
                   updated_at=? WHERE plan_id=?""",
                (datetime.now(timezone.utc).isoformat(), plan_id),
            )
            db.commit()
        return self.get_plan(plan_id)

    def get_plan(self, plan_id):
        with self._lock, self._session() as db:
            row = db.execute(
                "SELECT * FROM research_plans WHERE plan_id=?", (plan_id,)
            ).fetchone()
        if row is None:
            raise KeyError("Research plan not found.")
        return {
            "plan_id": row["plan_id"],
            "goal": row["goal"],
            "questions": json.loads(row["questions_json"]),
            "steps": json.loads(row["steps_json"]),
            "status": row["status"],
            "task_id": row["task_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "execution_note": "A plan is a proposal. Queue approval does not mean a worker executed it.",
        }

    def list_plans(self, *, limit=50):
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("limit must be an integer between 1 and 200.")
        with self._lock, self._session() as db:
            rows = db.execute(
                "SELECT plan_id FROM research_plans ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self.get_plan(row["plan_id"]) for row in rows]
