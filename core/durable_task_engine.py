"""Durable SQLite primitives for supervised agent task execution.

This module persists state but never executes task code or authorizes side effects.
"""
from __future__ import annotations
import json
import math
import re
import hashlib
import sqlite3
from contextlib import closing
import time
import uuid
from pathlib import Path
from typing import Any

MAX_JSON_BYTES = 1_000_000
TERMINAL = {"succeeded", "failed", "cancelled"}

class TaskEngineError(RuntimeError): pass
class TaskNotFound(TaskEngineError): pass
class TaskConflict(TaskEngineError): pass

class DurableTaskEngine:
    """Transactional task queue with idempotency, dependency gates, leases and events.

    Use a local filesystem for SQLite. This is not a distributed database.
    """
    def __init__(self, db_path: str | Path, *, clock=time.time):
        self.db_path, self.clock = str(db_path), clock
        self._memory_uri = self.db_path == ":memory:"
        self._anchor = None
        self._closed = False
        if self._memory_uri:
            # Each SQLite ":memory:" connection is a separate database. Keep
            # an anchor connection alive and share a unique in-memory URI so
            # the engine's per-operation connections see the same schema/data.
            self.db_path = f"file:yuki_tasks_{uuid.uuid4().hex}?mode=memory&cache=shared"
            self._anchor = sqlite3.connect(
                self.db_path, timeout=10, isolation_level=None, uri=True
            )
        else:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS goals(
              id TEXT PRIMARY KEY, state TEXT NOT NULL CHECK(state IN ('active','paused','cancelled','completed')),
              created_at REAL NOT NULL, updated_at REAL NOT NULL, owner_id TEXT);
            CREATE TABLE IF NOT EXISTS tasks(
              id TEXT PRIMARY KEY, goal_id TEXT NOT NULL REFERENCES goals(id),
              task_type TEXT NOT NULL, payload_json TEXT NOT NULL, result_json TEXT,
              state TEXT NOT NULL CHECK(state IN ('queued','running','succeeded','failed','cancelled')),
              idempotency_key TEXT NOT NULL, submission_fingerprint TEXT, attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL,
              available_at REAL NOT NULL, lease_owner TEXT, lease_until REAL, last_error TEXT,
              created_at REAL NOT NULL, updated_at REAL NOT NULL, UNIQUE(goal_id,idempotency_key));
            CREATE INDEX IF NOT EXISTS tasks_claim_idx ON tasks(state,available_at,created_at);
            CREATE INDEX IF NOT EXISTS tasks_goal_idx ON tasks(goal_id,created_at);
            CREATE TABLE IF NOT EXISTS dependencies(
              task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
              depends_on TEXT NOT NULL REFERENCES tasks(id), PRIMARY KEY(task_id,depends_on), CHECK(task_id<>depends_on));
            CREATE TABLE IF NOT EXISTS run_events(
              id INTEGER PRIMARY KEY AUTOINCREMENT, goal_id TEXT NOT NULL, task_id TEXT,
              event_type TEXT NOT NULL, detail_json TEXT NOT NULL, created_at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS events_goal_idx ON run_events(goal_id,id);
            CREATE TABLE IF NOT EXISTS goal_plans(
              goal_id TEXT PRIMARY KEY REFERENCES goals(id),
              plan_fingerprint TEXT NOT NULL, plan_json TEXT NOT NULL,
              created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS workforce_improvement_proposals(
              proposal_id TEXT PRIMARY KEY,
              proposal_fingerprint TEXT NOT NULL,
              proposal_json TEXT NOT NULL,
              state TEXT NOT NULL CHECK(state IN ('proposed','approved','rejected','completed')),
              created_at REAL NOT NULL,
              updated_at REAL NOT NULL,
              decided_by TEXT,
              decision_note TEXT,
              outcome_json TEXT);
            CREATE TABLE IF NOT EXISTS workforce_improvement_events(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              proposal_id TEXT NOT NULL REFERENCES workforce_improvement_proposals(proposal_id),
              event_type TEXT NOT NULL,
              actor_id TEXT NOT NULL,
              detail_json TEXT NOT NULL,
              created_at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS workforce_improvement_events_idx
              ON workforce_improvement_events(proposal_id,id);
            CREATE TABLE IF NOT EXISTS workforce_experiments(
              experiment_id TEXT PRIMARY KEY,
              proposal_id TEXT NOT NULL REFERENCES workforce_improvement_proposals(proposal_id),
              experiment_fingerprint TEXT NOT NULL,
              design_json TEXT NOT NULL,
              state TEXT NOT NULL CHECK(state IN ('proposed','approved','rejected')),
              created_at REAL NOT NULL,
              updated_at REAL NOT NULL,
              decided_by TEXT,
              decision_note TEXT);
            CREATE TABLE IF NOT EXISTS workforce_experiment_events(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              experiment_id TEXT NOT NULL REFERENCES workforce_experiments(experiment_id),
              event_type TEXT NOT NULL,
              actor_id TEXT NOT NULL,
              detail_json TEXT NOT NULL,
              created_at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS workforce_experiment_events_idx
              ON workforce_experiment_events(experiment_id,id);
            CREATE TABLE IF NOT EXISTS workforce_experiment_assignments(
              experiment_id TEXT NOT NULL REFERENCES workforce_experiments(experiment_id),
              task_id TEXT NOT NULL REFERENCES tasks(id),
              assignment TEXT NOT NULL CHECK(assignment IN ('control','treatment')),
              assignment_key TEXT NOT NULL,
              assigned_at REAL NOT NULL,
              PRIMARY KEY(experiment_id,task_id),
              UNIQUE(task_id));
            CREATE INDEX IF NOT EXISTS workforce_experiment_assignments_idx
              ON workforce_experiment_assignments(experiment_id,assignment);
            CREATE TABLE IF NOT EXISTS workforce_experiment_deliveries(
              experiment_id TEXT NOT NULL,
              task_id TEXT NOT NULL,
              delivery_state TEXT NOT NULL CHECK(delivery_state IN ('delivered','not_delivered','unknown')),
              intervention_version TEXT,
              evidence_reference TEXT,
              actor_id TEXT NOT NULL,
              created_at REAL NOT NULL,
              PRIMARY KEY(experiment_id,task_id),
              FOREIGN KEY(experiment_id,task_id)
                REFERENCES workforce_experiment_assignments(experiment_id,task_id),
              CHECK((delivery_state='delivered' AND intervention_version IS NOT NULL)
                 OR (delivery_state!='delivered' AND intervention_version IS NULL))
            );
            CREATE INDEX IF NOT EXISTS workforce_experiment_deliveries_idx
              ON workforce_experiment_deliveries(experiment_id,delivery_state);
            CREATE TABLE IF NOT EXISTS workforce_experiment_execution_events(
              event_id INTEGER PRIMARY KEY AUTOINCREMENT,
              experiment_id TEXT NOT NULL,
              task_id TEXT NOT NULL,
              event_type TEXT NOT NULL CHECK(event_type IN ('execution_started','execution_succeeded','execution_failed')),
              attempt INTEGER NOT NULL CHECK(attempt >= 1),
              worker_id TEXT NOT NULL,
              created_at REAL NOT NULL,
              FOREIGN KEY(experiment_id,task_id)
                REFERENCES workforce_experiment_assignments(experiment_id,task_id),
              UNIQUE(experiment_id,task_id,event_type,attempt)
            );
            CREATE INDEX IF NOT EXISTS workforce_experiment_execution_events_idx
              ON workforce_experiment_execution_events(experiment_id,event_type,created_at);
            CREATE TABLE IF NOT EXISTS workforce_experiment_treatment_exposures(
              exposure_id INTEGER PRIMARY KEY AUTOINCREMENT,
              experiment_id TEXT NOT NULL,
              task_id TEXT NOT NULL,
              attempt INTEGER NOT NULL CHECK(attempt >= 1),
              worker_id TEXT NOT NULL,
              intervention_version TEXT NOT NULL,
              skill_name TEXT NOT NULL,
              created_at REAL NOT NULL,
              FOREIGN KEY(experiment_id,task_id)
                REFERENCES workforce_experiment_assignments(experiment_id,task_id),
              UNIQUE(experiment_id,task_id,attempt)
            );
            CREATE INDEX IF NOT EXISTS workforce_experiment_treatment_exposures_idx
              ON workforce_experiment_treatment_exposures(experiment_id,created_at);
            CREATE TRIGGER IF NOT EXISTS workforce_experiment_treatment_exposures_no_update
            BEFORE UPDATE ON workforce_experiment_treatment_exposures
            BEGIN SELECT RAISE(ABORT,'treatment exposure events are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS workforce_experiment_treatment_exposures_no_delete
            BEFORE DELETE ON workforce_experiment_treatment_exposures
            BEGIN SELECT RAISE(ABORT,'treatment exposure events are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS workforce_experiment_execution_events_no_update
            BEFORE UPDATE ON workforce_experiment_execution_events
            BEGIN SELECT RAISE(ABORT,'experiment execution events are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS workforce_experiment_execution_events_no_delete
            BEFORE DELETE ON workforce_experiment_execution_events
            BEGIN SELECT RAISE(ABORT,'experiment execution events are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS workforce_experiment_deliveries_no_update
            BEFORE UPDATE ON workforce_experiment_deliveries
            BEGIN SELECT RAISE(ABORT,'experiment delivery attestations are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS workforce_experiment_deliveries_no_delete
            BEFORE DELETE ON workforce_experiment_deliveries
            BEGIN SELECT RAISE(ABORT,'experiment delivery attestations are immutable'); END;
            CREATE TABLE IF NOT EXISTS workforce_knowledge(
              knowledge_id TEXT PRIMARY KEY,
              fingerprint TEXT NOT NULL,
              knowledge_json TEXT NOT NULL,
              state TEXT NOT NULL CHECK(state IN ('candidate','verified','rejected')),
              source_task_id TEXT NOT NULL REFERENCES tasks(id),
              created_at REAL NOT NULL,
              updated_at REAL NOT NULL,
              reviewed_by TEXT,
              review_note TEXT,
              UNIQUE(source_task_id,fingerprint));
            CREATE TABLE IF NOT EXISTS workforce_knowledge_events(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              knowledge_id TEXT NOT NULL REFERENCES workforce_knowledge(knowledge_id),
              event_type TEXT NOT NULL,
              actor_id TEXT NOT NULL,
              detail_json TEXT NOT NULL,
              created_at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS workforce_knowledge_state_idx
              ON workforce_knowledge(state,updated_at);
            CREATE INDEX IF NOT EXISTS workforce_knowledge_events_idx
              ON workforce_knowledge_events(knowledge_id,id);
            CREATE TABLE IF NOT EXISTS workforce_knowledge_revisions(
              revision_id TEXT PRIMARY KEY,
              knowledge_id TEXT NOT NULL REFERENCES workforce_knowledge(knowledge_id),
              version INTEGER NOT NULL,
              knowledge_json TEXT NOT NULL,
              state TEXT NOT NULL CHECK(state IN ('proposed','approved','rejected')),
              created_at REAL NOT NULL,
              reviewed_at REAL,
              reviewed_by TEXT,
              review_note TEXT,
              UNIQUE(knowledge_id,version));
            CREATE TABLE IF NOT EXISTS goal_requests(
              owner_id TEXT NOT NULL, idempotency_key TEXT NOT NULL,
              request_fingerprint TEXT NOT NULL, goal_id TEXT NOT NULL REFERENCES goals(id),
              created_at REAL NOT NULL, PRIMARY KEY(owner_id,idempotency_key));
            """)
            # Existing databases need additive migrations. Old rows have no
            # trustworthy fingerprint, so replaying them is rejected safely.
            goal_columns = {row["name"] for row in c.execute("PRAGMA table_info(goals)")}
            if "owner_id" not in goal_columns:
                # Legacy goals have no trustworthy owner and intentionally remain NULL.
                c.execute("ALTER TABLE goals ADD COLUMN owner_id TEXT")
            task_columns = {row["name"] for row in c.execute("PRAGMA table_info(tasks)")}
            if "submission_fingerprint" not in task_columns:
                c.execute("ALTER TABLE tasks ADD COLUMN submission_fingerprint TEXT")

    def close(self):
        """Release engine-owned resources. Safe to call more than once."""
        if self._closed:
            return
        self._closed = True
        if self._anchor is not None:
            self._anchor.close()
            self._anchor = None

    def __enter__(self):
        if self._closed:
            raise RuntimeError("task engine is closed")
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False

    def _connect(self):
        if self._closed:
            raise RuntimeError("task engine is closed")
        c = sqlite3.connect(
            self.db_path, timeout=10, isolation_level=None, uri=self._memory_uri
        )
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA busy_timeout=10000")
        return c

    @staticmethod
    def _json(value):
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)
        if len(raw.encode("utf-8")) > MAX_JSON_BYTES:
            raise ValueError("JSON exceeds 1 MB limit")
        return raw

    @staticmethod
    def _decode(row):
        if row is None: return None
        d = dict(row)
        for k in ("payload_json", "result_json"):
            if d.get(k) is not None:
                d[k.removesuffix("_json")] = json.loads(d.pop(k))
        return d

    def _event(self, c, goal, task, kind, detail):
        c.execute("INSERT INTO run_events(goal_id,task_id,event_type,detail_json,created_at) VALUES(?,?,?,?,?)",
                  (goal, task, kind, self._json(detail), self.clock()))

    def create_goal(self, goal_id=None, *, owner_id=None):
        """Create a goal; owner_id is optional for trusted internal/legacy callers."""
        if owner_id is not None and (
            not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 256
        ):
            raise ValueError("owner_id must be non-empty text of at most 256 characters")
        goal_id, now = goal_id or str(uuid.uuid4()), self.clock()
        with closing(self._connect()) as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute(
                "INSERT INTO goals(id,state,created_at,updated_at,owner_id) VALUES(?,?,?,?,?)",
                (goal_id, "active", now, now, owner_id),
            )
            self._event(c,goal_id,None,"goal.created",{})
            c.commit()
        return goal_id

    def create_owned_goal(self, owner_id, idempotency_key, request):
        """Atomically create or replay an owner-scoped goal intake request.

        The request is canonically serialized and fingerprinted in-process;
        callers cannot supply a digest that accidentally omits request fields.
        """
        if not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 256:
            raise ValueError("owner_id must be non-empty text of at most 256 characters")
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 256:
            raise ValueError("idempotency_key must contain 1..256 characters")
        request_json = self._json(request)
        if len(request_json.encode("utf-8")) > 16_384:
            raise ValueError("goal request exceeds 16 KB limit")
        fingerprint = hashlib.sha256(request_json.encode("utf-8")).hexdigest()
        goal_id, now = str(uuid.uuid4()), self.clock()
        with closing(self._connect()) as c:
            c.execute("BEGIN IMMEDIATE")
            existing = c.execute(
                "SELECT request_fingerprint,goal_id FROM goal_requests "
                "WHERE owner_id=? AND idempotency_key=?",
                (owner_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing["request_fingerprint"] != fingerprint:
                    raise TaskConflict("goal idempotency key reused with a different request")
                goal = c.execute(
                    "SELECT * FROM goals WHERE id=? AND owner_id=?",
                    (existing["goal_id"], owner_id),
                ).fetchone()
                if goal is None:
                    raise TaskConflict("idempotent goal record is unavailable")
                c.commit()
                return {"goal": dict(goal), "created": False}
            c.execute(
                "INSERT INTO goals(id,state,created_at,updated_at,owner_id) VALUES(?,?,?,?,?)",
                (goal_id, "active", now, now, owner_id),
            )
            c.execute(
                "INSERT INTO goal_requests(owner_id,idempotency_key,request_fingerprint,goal_id,created_at) "
                "VALUES(?,?,?,?,?)",
                (owner_id, idempotency_key, fingerprint, goal_id, now),
            )
            self._event(c, goal_id, None, "goal.created", {})
            c.commit()
        return {"goal": self.get_goal(goal_id), "created": True}

    def submit(self, goal_id, task_type, payload, *, idempotency_key, dependencies=(), max_attempts=3, delay_seconds=0):
        if not isinstance(task_type,str) or not 1 <= len(task_type) <= 128: raise ValueError("invalid task_type")
        if not isinstance(idempotency_key,str) or not 1 <= len(idempotency_key) <= 256: raise ValueError("invalid idempotency_key")
        if not isinstance(max_attempts,int) or isinstance(max_attempts,bool) or not 1 <= max_attempts <= 20: raise ValueError("max_attempts must be 1..20")
        if isinstance(delay_seconds, bool) or not isinstance(delay_seconds,(int,float)) or not 0 <= delay_seconds <= 86400 or not math.isfinite(delay_seconds): raise ValueError("delay_seconds must be finite and 0..86400")
        if not isinstance(dependencies, (list, tuple)):
            raise ValueError("dependencies must be a list or tuple of task IDs")
        if len(dependencies) > 100:
            raise ValueError("at most 100 dependencies allowed")
        if any(not isinstance(dep, str) or not 1 <= len(dep) <= 256 for dep in dependencies):
            raise ValueError("each dependency must be a non-empty task ID string of at most 256 characters")
        payload_json, deps, now = self._json(payload), tuple(sorted(set(dependencies))), self.clock()
        submission_fingerprint = self._json({
            "task_type": task_type,
            "payload_json": payload_json,
            "dependencies": list(deps),
            "max_attempts": max_attempts,
            "delay_seconds": float(delay_seconds),
        })
        task_id = str(uuid.uuid4())
        with closing(self._connect()) as c:
            c.execute("BEGIN IMMEDIATE")
            goal = c.execute("SELECT state FROM goals WHERE id=?",(goal_id,)).fetchone()
            if goal is None: raise TaskNotFound("goal not found")
            old = c.execute("SELECT * FROM tasks WHERE goal_id=? AND idempotency_key=?",(goal_id,idempotency_key)).fetchone()
            if old is not None:
                # Exact replays stay idempotent even after the goal becomes
                # terminal; returning an existing task never reopens the goal.
                if old["submission_fingerprint"] is None or old["submission_fingerprint"] != submission_fingerprint:
                    raise TaskConflict("idempotency key reused with different or unverifiable submission options")
                c.commit()
                return self._decode(old)
            if goal["state"]!="active": raise TaskConflict("goal is not active")
            for dep in deps:
                d = c.execute("SELECT goal_id FROM tasks WHERE id=?",(dep,)).fetchone()
                if d is None or d["goal_id"]!=goal_id: raise TaskConflict("dependency must belong to same goal")
            c.execute("INSERT INTO tasks(id,goal_id,task_type,payload_json,state,idempotency_key,submission_fingerprint,max_attempts,available_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      (task_id,goal_id,task_type,payload_json,"queued",idempotency_key,submission_fingerprint,max_attempts,now+delay_seconds,now,now))
            for dep in deps: c.execute("INSERT INTO dependencies VALUES(?,?)",(task_id,dep))
            c.execute("UPDATE goals SET updated_at=? WHERE id=?",(now,goal_id))
            self._event(c,goal_id,task_id,"task.queued",{"task_type":task_type,"dependencies":list(deps)})
            c.commit()
        return self.get_task(task_id)

    def bind_goal_plan(self, goal_id, plan):
        """Persist the first CEO plan and reject any later plan drift for that goal."""
        if not isinstance(goal_id, str) or not goal_id.strip():
            raise ValueError("goal_id must be non-empty text")
        plan_json = self._json(plan)
        if len(plan_json.encode("utf-8")) > 65_536:
            raise ValueError("CEO plan exceeds 64 KB limit")
        fingerprint = hashlib.sha256(plan_json.encode("utf-8")).hexdigest()
        now = self.clock()
        with closing(self._connect()) as c:
            c.execute("BEGIN IMMEDIATE")
            goal = c.execute("SELECT id FROM goals WHERE id=?", (goal_id,)).fetchone()
            if goal is None:
                raise TaskNotFound("goal not found")
            existing = c.execute(
                "SELECT plan_fingerprint,plan_json FROM goal_plans WHERE goal_id=?",
                (goal_id,),
            ).fetchone()
            if existing is not None:
                if existing["plan_fingerprint"] != fingerprint or existing["plan_json"] != plan_json:
                    raise TaskConflict("CEO plan changed for an existing goal")
                c.commit()
                return {"created": False, "plan": json.loads(existing["plan_json"])}
            c.execute(
                "INSERT INTO goal_plans(goal_id,plan_fingerprint,plan_json,created_at) VALUES(?,?,?,?)",
                (goal_id, fingerprint, plan_json, now),
            )
            self._event(c, goal_id, None, "goal.plan_bound", {
                "plan_fingerprint": fingerprint,
                "task_count": len(plan) if isinstance(plan, list) else None,
            })
            c.commit()
        return {"created": True, "plan": json.loads(plan_json)}

    def create_knowledge_candidate(self, knowledge_id, source_task_id, knowledge):
        """Persist a proposed lesson only when its source task has independent review evidence."""
        if not isinstance(knowledge_id, str) or not 1 <= len(knowledge_id) <= 256:
            raise ValueError("knowledge_id must contain 1..256 characters")
        if not isinstance(source_task_id, str) or not 1 <= len(source_task_id) <= 256:
            raise ValueError("source_task_id must contain 1..256 characters")
        if not isinstance(knowledge, dict) or set(knowledge) != {"title", "lesson", "domain", "tags"}:
            raise ValueError("knowledge must contain exactly title, lesson, domain, and tags")
        for key, maximum in (("title", 200), ("lesson", 4000), ("domain", 120)):
            value = knowledge[key]
            if not isinstance(value, str) or not value.strip() or len(value) > maximum:
                raise ValueError(f"invalid knowledge {key}")
        tags = knowledge["tags"]
        if (not isinstance(tags, list) or len(tags) > 20
                or any(not isinstance(tag, str) or not tag.strip() or len(tag) > 64 for tag in tags)
                or len({tag.casefold() for tag in tags}) != len(tags)):
            raise ValueError("knowledge tags must be unique bounded strings")
        # Conservative high-confidence screening; this is not a complete DLP system.
        sensitive_patterns = (
            r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
            r"(?i)\b(?:api[_-]?key|access[_-]?token|secret|password)\s*[:=]\s*\S+",
            r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}",
            r"\bAKIA[0-9A-Z]{16}\b",
            r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
        )
        searchable_text = " ".join((knowledge["title"], knowledge["lesson"], knowledge["domain"], *tags))
        if any(re.search(pattern, searchable_text) for pattern in sensitive_patterns):
            raise ValueError("knowledge contains a credential or personal contact identifier")
        knowledge_json = self._json(knowledge)
        if len(knowledge_json.encode("utf-8")) > 8192:
            raise ValueError("knowledge candidate exceeds 8 KB")
        fingerprint = hashlib.sha256(knowledge_json.encode("utf-8")).hexdigest()
        now = self.clock()
        with closing(self._connect()) as c:
            c.execute("BEGIN IMMEDIATE")
            task = c.execute("SELECT state,result_json FROM tasks WHERE id=?", (source_task_id,)).fetchone()
            if task is None:
                raise TaskNotFound("source task not found")
            if task["state"] != "succeeded" or not task["result_json"]:
                raise TaskConflict("knowledge requires a succeeded source task")
            result = json.loads(task["result_json"])
            evidence = result.get("review_evidence") if isinstance(result, dict) else None
            if (not isinstance(evidence, dict)
                    or evidence.get("approved") is not True
                    or not isinstance(evidence.get("reviewer_id"), str)
                    or not evidence["reviewer_id"].strip()
                    or not isinstance(evidence.get("status"), str)
                    or not evidence["status"].strip()):
                raise TaskConflict("knowledge requires persisted independent review evidence")
            existing = c.execute(
                "SELECT knowledge_id,fingerprint,knowledge_json FROM workforce_knowledge WHERE source_task_id=? AND fingerprint=?",
                (source_task_id, fingerprint),
            ).fetchone()
            if existing:
                if existing["knowledge_json"] != knowledge_json:
                    raise TaskConflict("knowledge fingerprint collision")
                c.commit()
                return {"created": False, **self.get_knowledge(existing["knowledge_id"])}
            if c.execute("SELECT 1 FROM workforce_knowledge WHERE knowledge_id=?", (knowledge_id,)).fetchone():
                raise TaskConflict("knowledge_id already exists")
            c.execute(
                "INSERT INTO workforce_knowledge(knowledge_id,fingerprint,knowledge_json,state,source_task_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (knowledge_id, fingerprint, knowledge_json, "candidate", source_task_id, now, now),
            )
            c.execute(
                "INSERT INTO workforce_knowledge_events(knowledge_id,event_type,actor_id,detail_json,created_at) VALUES(?,?,?,?,?)",
                (knowledge_id, "knowledge.proposed", "system", self._json({"source_task_id": source_task_id, "source_reviewer_id": evidence["reviewer_id"]}), now),
            )
            c.commit()
        return {"created": True, **self.get_knowledge(knowledge_id)}

    def transition_knowledge(self, knowledge_id, *, expected_state, new_state, actor_id, review_note=None):
        if not isinstance(knowledge_id, str) or not 1 <= len(knowledge_id) <= 256:
            raise ValueError("invalid knowledge_id")
        if not isinstance(actor_id, str) or not actor_id.strip() or len(actor_id) > 256:
            raise ValueError("actor_id must be non-empty bounded text")
        if not isinstance(review_note, (str, type(None))) or (review_note is not None and len(review_note) > 2000):
            raise ValueError("review_note must be at most 2000 characters")
        if expected_state != "candidate" or new_state not in {"verified", "rejected"}:
            raise TaskConflict("invalid knowledge state transition")
        now = self.clock()
        with closing(self._connect()) as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT state FROM workforce_knowledge WHERE knowledge_id=?", (knowledge_id,)).fetchone()
            if row is None:
                raise TaskNotFound("knowledge record not found")
            if row["state"] != expected_state:
                raise TaskConflict("knowledge state changed")
            c.execute(
                "UPDATE workforce_knowledge SET state=?,updated_at=?,reviewed_by=?,review_note=? WHERE knowledge_id=?",
                (new_state, now, actor_id, review_note, knowledge_id),
            )
            c.execute(
                "INSERT INTO workforce_knowledge_events(knowledge_id,event_type,actor_id,detail_json,created_at) VALUES(?,?,?,?,?)",
                (knowledge_id, "knowledge." + new_state, actor_id, self._json({"review_note": review_note}), now),
            )
            c.commit()
        return self.get_knowledge(knowledge_id)

    def get_knowledge(self, knowledge_id):
        with closing(self._connect()) as c:
            row = c.execute("SELECT * FROM workforce_knowledge WHERE knowledge_id=?", (knowledge_id,)).fetchone()
            if row is None:
                raise TaskNotFound("knowledge record not found")
            events = c.execute(
                "SELECT * FROM workforce_knowledge_events WHERE knowledge_id=? ORDER BY id",
                (knowledge_id,),
            ).fetchall()
        result = dict(row)
        result["knowledge"] = json.loads(result.pop("knowledge_json"))
        result.pop("fingerprint")
        result["events"] = [
            {"id": event["id"], "event_type": event["event_type"], "actor_id": event["actor_id"],
             "detail": json.loads(event["detail_json"]), "created_at": event["created_at"]}
            for event in events
        ]
        return result

    def list_verified_knowledge(self, *, limit=100, domain=None):
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("limit must be an integer between 1 and 500")
        if domain is not None and (not isinstance(domain, str) or not domain.strip() or len(domain) > 120):
            raise ValueError("domain must be bounded non-empty text")
        with closing(self._connect()) as c:
            if domain is None:
                rows = c.execute(
                    "SELECT knowledge_id FROM workforce_knowledge WHERE state='verified' ORDER BY updated_at DESC,knowledge_id LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT knowledge_id FROM workforce_knowledge WHERE state='verified' AND json_extract(knowledge_json,'$.domain')=? ORDER BY updated_at DESC,knowledge_id LIMIT ?",
                    (domain, limit),
                ).fetchall()
        return [self.get_knowledge(row["knowledge_id"]) for row in rows]

    def create_knowledge_revision(self, knowledge_id, revision_id, knowledge):
        """Propose a bounded revision to an existing verified lesson; never activates it."""
        if not isinstance(knowledge_id, str) or not 1 <= len(knowledge_id) <= 256:
            raise ValueError("invalid knowledge_id")
        if (not isinstance(revision_id, str) or not 1 <= len(revision_id) <= 256
                or revision_id == f"{knowledge_id}:v1"):
            raise ValueError("invalid or reserved revision_id")
        if not isinstance(knowledge, dict) or set(knowledge) != {"title", "lesson", "domain", "tags"}:
            raise ValueError("knowledge revision has an invalid schema")
        for key, maximum in (("title", 200), ("lesson", 4000), ("domain", 120)):
            value = knowledge[key]
            if not isinstance(value, str) or not value.strip() or len(value) > maximum:
                raise ValueError(f"invalid knowledge {key}")
        tags = knowledge["tags"]
        if (not isinstance(tags, list) or len(tags) > 20
                or any(not isinstance(tag, str) or not tag.strip() or len(tag) > 64 for tag in tags)
                or len({tag.casefold() for tag in tags}) != len(tags)):
            raise ValueError("knowledge tags must be unique bounded strings")
        sensitive_patterns = (
            r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
            r"(?i)\b(?:api[_-]?key|access[_-]?token|secret|password)\s*[:=]\s*\S+",
            r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}",
            r"\bAKIA[0-9A-Z]{16}\b",
            r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
        )
        searchable = " ".join((knowledge["title"], knowledge["lesson"], knowledge["domain"], *tags))
        if any(re.search(pattern, searchable) for pattern in sensitive_patterns):
            raise ValueError("knowledge contains a credential or personal contact identifier")
        knowledge_json = self._json(knowledge)
        if len(knowledge_json.encode("utf-8")) > 8192:
            raise ValueError("knowledge revision exceeds 8 KB")
        now = self.clock()
        with closing(self._connect()) as c:
            c.execute("BEGIN IMMEDIATE")
            current = c.execute(
                "SELECT * FROM workforce_knowledge WHERE knowledge_id=?", (knowledge_id,)
            ).fetchone()
            if current is None:
                raise TaskNotFound("knowledge record not found")
            if current["state"] != "verified":
                raise TaskConflict("only verified knowledge can be revised")
            existing = c.execute(
                "SELECT * FROM workforce_knowledge_revisions WHERE revision_id=?", (revision_id,)
            ).fetchone()
            if existing:
                if existing["knowledge_id"] != knowledge_id or existing["knowledge_json"] != knowledge_json:
                    raise TaskConflict("revision ID reused with different content")
                c.commit()
                return {"created": False, **self.get_knowledge_revision(revision_id)}
            history = c.execute(
                "SELECT 1 FROM workforce_knowledge_revisions WHERE knowledge_id=? LIMIT 1",
                (knowledge_id,),
            ).fetchone()
            if history is None:
                # Preserve the original verified content as immutable version 1.
                c.execute(
                    "INSERT INTO workforce_knowledge_revisions(revision_id,knowledge_id,version,knowledge_json,state,created_at,reviewed_at,reviewed_by,review_note) VALUES(?,?,?,?,?,?,?,?,?)",
                    (f"{knowledge_id}:v1", knowledge_id, 1, current["knowledge_json"],
                     "approved", current["created_at"], current["updated_at"],
                     current["reviewed_by"] or "system", "Original verified version"),
                )
            version = c.execute(
                "SELECT COALESCE(MAX(version),0)+1 AS next_version FROM workforce_knowledge_revisions WHERE knowledge_id=?",
                (knowledge_id,),
            ).fetchone()["next_version"]
            c.execute(
                "INSERT INTO workforce_knowledge_revisions(revision_id,knowledge_id,version,knowledge_json,state,created_at) VALUES(?,?,?,?,?,?)",
                (revision_id, knowledge_id, version, knowledge_json, "proposed", now),
            )
            c.execute(
                "INSERT INTO workforce_knowledge_events(knowledge_id,event_type,actor_id,detail_json,created_at) VALUES(?,?,?,?,?)",
                (knowledge_id, "knowledge.revision_proposed", "system", self._json({"revision_id": revision_id, "version": version}), now),
            )
            c.commit()
        return {"created": True, **self.get_knowledge_revision(revision_id)}

    def transition_knowledge_revision(self, revision_id, *, expected_state, new_state, actor_id, review_note=None):
        if not isinstance(revision_id, str) or not 1 <= len(revision_id) <= 256:
            raise ValueError("invalid revision_id")
        if not isinstance(actor_id, str) or not actor_id.strip() or len(actor_id) > 256:
            raise ValueError("actor_id must be non-empty bounded text")
        if not isinstance(review_note, (str, type(None))) or (review_note is not None and len(review_note) > 2000):
            raise ValueError("review_note must be at most 2000 characters")
        if expected_state != "proposed" or new_state not in {"approved", "rejected"}:
            raise TaskConflict("invalid revision transition")
        now = self.clock()
        with closing(self._connect()) as c:
            c.execute("BEGIN IMMEDIATE")
            revision = c.execute(
                "SELECT * FROM workforce_knowledge_revisions WHERE revision_id=?", (revision_id,)
            ).fetchone()
            if revision is None:
                raise TaskNotFound("knowledge revision not found")
            if revision["state"] != expected_state:
                raise TaskConflict("knowledge revision state changed")
            if new_state == "approved":
                current = c.execute(
                    "SELECT state FROM workforce_knowledge WHERE knowledge_id=?", (revision["knowledge_id"],)
                ).fetchone()
                if current is None or current["state"] != "verified":
                    raise TaskConflict("parent knowledge is no longer verified")
                approved = c.execute(
                    "SELECT 1 FROM workforce_knowledge_revisions WHERE knowledge_id=? AND state='approved'",
                    (revision["knowledge_id"],),
                ).fetchone()
                if approved:
                    # A newer revision must be proposed against the current version, never concurrently promoted.
                    latest = c.execute(
                        "SELECT MAX(version) AS version FROM workforce_knowledge_revisions WHERE knowledge_id=? AND state='approved'",
                        (revision["knowledge_id"],),
                    ).fetchone()["version"]
                    if revision["version"] <= latest:
                        raise TaskConflict("revision is stale")
                content = json.loads(revision["knowledge_json"])
                fingerprint = hashlib.sha256(revision["knowledge_json"].encode("utf-8")).hexdigest()
                c.execute(
                    "UPDATE workforce_knowledge SET knowledge_json=?,fingerprint=?,updated_at=? WHERE knowledge_id=?",
                    (revision["knowledge_json"], fingerprint, now, revision["knowledge_id"]),
                )
            c.execute(
                "UPDATE workforce_knowledge_revisions SET state=?,reviewed_at=?,reviewed_by=?,review_note=? WHERE revision_id=?",
                (new_state, now, actor_id, review_note, revision_id),
            )
            c.execute(
                "INSERT INTO workforce_knowledge_events(knowledge_id,event_type,actor_id,detail_json,created_at) VALUES(?,?,?,?,?)",
                (revision["knowledge_id"], "knowledge.revision_" + new_state, actor_id,
                 self._json({"revision_id": revision_id, "version": revision["version"], "review_note": review_note}), now),
            )
            c.commit()
        return self.get_knowledge_revision(revision_id)

    def get_knowledge_revision(self, revision_id):
        with closing(self._connect()) as c:
            row = c.execute(
                "SELECT * FROM workforce_knowledge_revisions WHERE revision_id=?", (revision_id,)
            ).fetchone()
        if row is None:
            raise TaskNotFound("knowledge revision not found")
        result = dict(row)
        result["knowledge"] = json.loads(result.pop("knowledge_json"))
        return result

    def list_knowledge_revisions(self, knowledge_id):
        with closing(self._connect()) as c:
            rows = c.execute(
                "SELECT revision_id FROM workforce_knowledge_revisions WHERE knowledge_id=? ORDER BY version",
                (knowledge_id,),
            ).fetchall()
        return [self.get_knowledge_revision(row["revision_id"]) for row in rows]

    def create_improvement_proposal(self, proposal_id, proposal):
        """Create an immutable, idempotent CEO improvement proposal.

        This is storage, not authorization: callers must enforce actor permissions
        before invoking decision transitions.
        """
        if not isinstance(proposal_id, str) or not 1 <= len(proposal_id) <= 256:
            raise ValueError("proposal_id must contain 1..256 characters")
        if not isinstance(proposal, dict) or not proposal:
            raise ValueError("proposal must be a non-empty object")
        proposal_json = self._json(proposal)
        if len(proposal_json.encode("utf-8")) > 65_536:
            raise ValueError("proposal exceeds 64 KB limit")
        fingerprint = hashlib.sha256(proposal_json.encode("utf-8")).hexdigest()
        now = self.clock()
        with closing(self._connect()) as c:
            c.execute("BEGIN IMMEDIATE")
            existing = c.execute(
                "SELECT proposal_fingerprint,proposal_json FROM workforce_improvement_proposals WHERE proposal_id=?",
                (proposal_id,),
            ).fetchone()
            if existing is not None:
                if existing["proposal_fingerprint"] != fingerprint or existing["proposal_json"] != proposal_json:
                    raise TaskConflict("proposal ID reused with different proposal content")
                c.commit()
                return {"created": False, **self.get_improvement_proposal(proposal_id)}
            c.execute(
                "INSERT INTO workforce_improvement_proposals(proposal_id,proposal_fingerprint,proposal_json,state,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (proposal_id, fingerprint, proposal_json, "proposed", now, now),
            )
            c.execute(
                "INSERT INTO workforce_improvement_events(proposal_id,event_type,actor_id,detail_json,created_at) VALUES(?,?,?,?,?)",
                (proposal_id, "proposal.created", "system", self._json({"fingerprint": fingerprint}), now),
            )
            c.commit()
        return {"created": True, **self.get_improvement_proposal(proposal_id)}

    def transition_improvement_proposal(
        self, proposal_id, *, expected_state, new_state, actor_id,
        decision_note=None, outcome=None,
    ):
        """Apply a guarded proposal transition and append actor-attributed history."""
        if not isinstance(proposal_id, str) or not 1 <= len(proposal_id) <= 256:
            raise ValueError("invalid proposal_id")
        if not isinstance(actor_id, str) or not actor_id.strip() or len(actor_id) > 256:
            raise ValueError("actor_id must be non-empty text of at most 256 characters")
        if not isinstance(decision_note, (str, type(None))) or (decision_note is not None and len(decision_note) > 2000):
            raise ValueError("decision_note must be at most 2000 characters")
        if new_state not in {"approved", "rejected", "completed"}:
            raise ValueError("invalid proposal transition target")
        if outcome is not None and not isinstance(outcome, dict):
            raise ValueError("outcome must be an object")
        outcome_json = self._json(outcome) if outcome is not None else None
        if outcome_json is not None and len(outcome_json.encode("utf-8")) > 16_384:
            raise ValueError("outcome exceeds 16 KB limit")
        now = self.clock()
        with closing(self._connect()) as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute(
                "SELECT * FROM workforce_improvement_proposals WHERE proposal_id=?",
                (proposal_id,),
            ).fetchone()
            if row is None:
                raise TaskNotFound("improvement proposal not found")
            if row["state"] != expected_state:
                raise TaskConflict("improvement proposal state changed")
            allowed = {"proposed": {"approved", "rejected"}, "approved": {"completed"}}
            if new_state not in allowed.get(expected_state, set()):
                raise TaskConflict("invalid improvement proposal state transition")
            if new_state == "completed" and outcome_json is None:
                raise ValueError("completed proposals require an outcome object")
            if new_state != "completed" and outcome_json is not None:
                raise ValueError("outcome is only accepted when completing a proposal")
            c.execute(
                "UPDATE workforce_improvement_proposals SET state=?,updated_at=?,decided_by=?,decision_note=?,outcome_json=? WHERE proposal_id=?",
                (new_state, now, actor_id, decision_note, outcome_json, proposal_id),
            )
            detail = {"from_state": expected_state, "to_state": new_state, "decision_note": decision_note}
            if outcome_json is not None:
                detail["outcome"] = json.loads(outcome_json)
            c.execute(
                "INSERT INTO workforce_improvement_events(proposal_id,event_type,actor_id,detail_json,created_at) VALUES(?,?,?,?,?)",
                (proposal_id, "proposal." + new_state, actor_id, self._json(detail), now),
            )
            c.commit()
        return self.get_improvement_proposal(proposal_id)

    def get_improvement_proposal(self, proposal_id):
        with closing(self._connect()) as c:
            row = c.execute(
                "SELECT * FROM workforce_improvement_proposals WHERE proposal_id=?",
                (proposal_id,),
            ).fetchone()
            if row is None:
                raise TaskNotFound("improvement proposal not found")
            events = c.execute(
                "SELECT * FROM workforce_improvement_events WHERE proposal_id=? ORDER BY id",
                (proposal_id,),
            ).fetchall()
        result = dict(row)
        result["proposal"] = json.loads(result.pop("proposal_json"))
        outcome_json = result.pop("outcome_json")
        result["outcome"] = json.loads(outcome_json) if outcome_json is not None else None
        result.pop("proposal_fingerprint")
        result["events"] = [
            {"id": item["id"], "event_type": item["event_type"], "actor_id": item["actor_id"],
             "detail": json.loads(item["detail_json"]), "created_at": item["created_at"]}
            for item in events
        ]
        return result

    def list_improvement_proposals(self, *, limit=100):
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("limit must be an integer between 1 and 500")
        with closing(self._connect()) as c:
            rows = c.execute(
                "SELECT proposal_id FROM workforce_improvement_proposals ORDER BY updated_at DESC,proposal_id LIMIT ?",
                (limit,),
            ).fetchall()
        return [self.get_improvement_proposal(row["proposal_id"]) for row in rows]


    def create_workforce_experiment(self, experiment_id, proposal_id, design):
        """Persist a bounded experiment design linked to an approved improvement proposal.

        Approval records permission to proceed with later implementation planning only;
        this method does not assign tasks, alter production behavior, or run experiments.
        """
        if not isinstance(experiment_id, str) or not 1 <= len(experiment_id) <= 256:
            raise ValueError("invalid experiment_id")
        if not isinstance(proposal_id, str) or not 1 <= len(proposal_id) <= 256:
            raise ValueError("invalid proposal_id")
        if not isinstance(design, dict) or set(design) != {
            "hypothesis", "intervention", "primary_metric", "preferred_direction",
            "design_type", "eligible_population", "guardrails",
            "evaluation_window_days", "minimum_sample_size",
        }:
            raise ValueError("invalid experiment design schema")
        text_limits = {
            "hypothesis": 2000, "intervention": 2000, "primary_metric": 160,
        }
        for key, maximum in text_limits.items():
            value = design[key]
            if not isinstance(value, str) or not value.strip() or len(value) > maximum:
                raise ValueError("invalid experiment design field: " + key)
        population = design["eligible_population"]
        if isinstance(population, str):
            if not population.strip() or len(population) > 1000:
                raise ValueError("invalid experiment eligible_population")
        elif isinstance(population, dict):
            if set(population) != {"description", "task_types"}:
                raise ValueError("eligible_population requires description and task_types")
            description, task_types = population["description"], population["task_types"]
            if (not isinstance(description, str) or not description.strip() or len(description) > 1000
                    or not isinstance(task_types, list) or not 1 <= len(task_types) <= 50
                    or any(not isinstance(item, str) or not item.strip() or len(item) > 128 for item in task_types)
                    or len({item.casefold() for item in task_types}) != len(task_types)):
                raise ValueError("invalid structured eligible_population")
        else:
            raise ValueError("invalid experiment eligible_population")
        if design["preferred_direction"] not in {"increase", "decrease"}:
            raise ValueError("invalid preferred_direction")
        if design["design_type"] not in {"randomized_controlled", "pre_post_descriptive"}:
            raise ValueError("invalid design_type")
        if (not isinstance(design["guardrails"], list) or len(design["guardrails"]) > 20
                or any(not isinstance(item, str) or not item.strip() or len(item) > 240
                       for item in design["guardrails"])):
            raise ValueError("invalid guardrails")
        for key, low, high in (("evaluation_window_days", 1, 90), ("minimum_sample_size", 2, 100000)):
            if type(design[key]) is not int or not low <= design[key] <= high:
                raise ValueError("invalid " + key)
        design_json = self._json(design)
        if len(design_json.encode("utf-8")) > 8192:
            raise ValueError("experiment design exceeds 8 KB")
        fingerprint = hashlib.sha256((proposal_id + "\\0" + design_json).encode("utf-8")).hexdigest()
        now = self.clock()
        with closing(self._connect()) as c:
            c.execute("BEGIN IMMEDIATE")
            proposal = c.execute(
                "SELECT state FROM workforce_improvement_proposals WHERE proposal_id=?",
                (proposal_id,),
            ).fetchone()
            if proposal is None:
                raise TaskNotFound("linked improvement proposal not found")
            if proposal["state"] != "approved":
                raise TaskConflict("experiment requires an approved improvement proposal")
            existing = c.execute(
                "SELECT experiment_fingerprint FROM workforce_experiments WHERE experiment_id=?",
                (experiment_id,),
            ).fetchone()
            if existing is not None:
                if existing["experiment_fingerprint"] != fingerprint:
                    raise TaskConflict("experiment ID reused with different design")
                c.commit()
                return {"created": False, **self.get_workforce_experiment(experiment_id)}
            c.execute(
                "INSERT INTO workforce_experiments(experiment_id,proposal_id,experiment_fingerprint,design_json,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (experiment_id, proposal_id, fingerprint, design_json, "proposed", now, now),
            )
            c.execute(
                "INSERT INTO workforce_experiment_events(experiment_id,event_type,actor_id,detail_json,created_at) VALUES(?,?,?,?,?)",
                (experiment_id, "experiment.created", "system",
                 self._json({"proposal_id": proposal_id, "fingerprint": fingerprint}), now),
            )
            c.commit()
        return {"created": True, **self.get_workforce_experiment(experiment_id)}

    def transition_workforce_experiment(
        self, experiment_id, *, expected_state, new_state, actor_id, decision_note=None
    ):
        if not isinstance(experiment_id, str) or not 1 <= len(experiment_id) <= 256:
            raise ValueError("invalid experiment_id")
        if not isinstance(actor_id, str) or not actor_id.strip() or len(actor_id) > 256:
            raise ValueError("invalid actor_id")
        if decision_note is not None and (
            not isinstance(decision_note, str) or len(decision_note) > 2000
        ):
            raise ValueError("invalid decision_note")
        if new_state not in {"approved", "rejected"}:
            raise ValueError("invalid experiment transition target")
        with closing(self._connect()) as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute(
                "SELECT state FROM workforce_experiments WHERE experiment_id=?",
                (experiment_id,),
            ).fetchone()
            if row is None:
                raise TaskNotFound("experiment not found")
            if row["state"] != expected_state:
                raise TaskConflict("experiment state changed")
            if expected_state != "proposed" or new_state not in {"approved", "rejected"}:
                raise TaskConflict("invalid experiment state transition")
            now = self.clock()
            c.execute(
                "UPDATE workforce_experiments SET state=?,updated_at=?,decided_by=?,decision_note=? WHERE experiment_id=?",
                (new_state, now, actor_id, decision_note, experiment_id),
            )
            c.execute(
                "INSERT INTO workforce_experiment_events(experiment_id,event_type,actor_id,detail_json,created_at) VALUES(?,?,?,?,?)",
                (experiment_id, "experiment." + new_state, actor_id,
                 self._json({"from_state": expected_state, "to_state": new_state,
                             "decision_note": decision_note}), now),
            )
            c.commit()
        return self.get_workforce_experiment(experiment_id)

    def get_workforce_experiment(self, experiment_id):
        with closing(self._connect()) as c:
            row = c.execute(
                "SELECT * FROM workforce_experiments WHERE experiment_id=?",
                (experiment_id,),
            ).fetchone()
            if row is None:
                raise TaskNotFound("experiment not found")
            events = c.execute(
                "SELECT * FROM workforce_experiment_events WHERE experiment_id=? ORDER BY id",
                (experiment_id,),
            ).fetchall()
        result = dict(row)
        result["design"] = json.loads(result.pop("design_json"))
        result.pop("experiment_fingerprint")
        result["events"] = [
            {"id": event["id"], "event_type": event["event_type"],
             "actor_id": event["actor_id"], "detail": json.loads(event["detail_json"]),
             "created_at": event["created_at"]}
            for event in events
        ]
        return result

    def list_workforce_experiments(self, *, limit=100):
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("limit must be an integer between 1 and 500")
        with closing(self._connect()) as c:
            rows = c.execute(
                "SELECT experiment_id FROM workforce_experiments ORDER BY updated_at DESC,experiment_id LIMIT ?",
                (limit,),
            ).fetchall()
        return [self.get_workforce_experiment(row["experiment_id"]) for row in rows]

    def assign_workforce_experiment_task(self, experiment_id, task_id):
        """Persist deterministic 1:1 assignment metadata; never executes an intervention."""
        if not isinstance(experiment_id, str) or not 1 <= len(experiment_id) <= 256:
            raise ValueError("invalid experiment_id")
        if not isinstance(task_id, str) or not 1 <= len(task_id) <= 256:
            raise ValueError("invalid task_id")
        with closing(self._connect()) as c:
            c.execute("BEGIN IMMEDIATE")
            experiment = c.execute(
                "SELECT state,design_json FROM workforce_experiments WHERE experiment_id=?",
                (experiment_id,),
            ).fetchone()
            if experiment is None:
                raise TaskNotFound("experiment not found")
            if experiment["state"] != "approved":
                raise TaskConflict("experiment must be approved before assignment")
            design = json.loads(experiment["design_json"])
            if design["design_type"] != "randomized_controlled":
                raise TaskConflict("task assignment requires randomized_controlled design")
            population = design["eligible_population"]
            if not isinstance(population, dict) or set(population) != {"description", "task_types"}:
                raise TaskConflict("randomized assignment requires structured task-type eligibility")
            task = c.execute("SELECT id,task_type FROM tasks WHERE id=?", (task_id,)).fetchone()
            if task is None:
                raise TaskNotFound("task not found")
            allowed_types = {item.casefold() for item in population["task_types"]}
            if task["task_type"].casefold() not in allowed_types:
                raise TaskConflict("task does not satisfy experiment task-type eligibility")
            existing = c.execute(
                "SELECT experiment_id,assignment,assignment_key FROM workforce_experiment_assignments WHERE task_id=?",
                (task_id,),
            ).fetchone()
            if existing is not None:
                if existing["experiment_id"] != experiment_id:
                    raise TaskConflict("task already assigned to another experiment")
                c.commit()
                return {"created": False, "experiment_id": experiment_id, "task_id": task_id,
                        "assignment": existing["assignment"], "assignment_key": existing["assignment_key"]}
            assignment_key = hashlib.sha256((experiment_id + "\\0" + task_id).encode("utf-8")).hexdigest()
            arm = "treatment" if int(assignment_key[:8], 16) % 2 else "control"
            now = self.clock()
            c.execute(
                "INSERT INTO workforce_experiment_assignments(experiment_id,task_id,assignment,assignment_key,assigned_at) VALUES(?,?,?,?,?)",
                (experiment_id, task_id, arm, assignment_key, now),
            )
            c.execute(
                "INSERT INTO workforce_experiment_events(experiment_id,event_type,actor_id,detail_json,created_at) VALUES(?,?,?,?,?)",
                (experiment_id, "experiment.task_assigned", "system",
                 self._json({"task_id": task_id, "assignment": arm, "assignment_key": assignment_key}), now),
            )
            c.commit()
        return {"created": True, "experiment_id": experiment_id, "task_id": task_id,
                "assignment": arm, "assignment_key": assignment_key}

    def list_workforce_experiment_assignments(self, experiment_id, *, limit=500):
        if not isinstance(experiment_id, str) or not 1 <= len(experiment_id) <= 256:
            raise ValueError("invalid experiment_id")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("limit must be an integer between 1 and 500")
        with closing(self._connect()) as c:
            exists = c.execute("SELECT 1 FROM workforce_experiments WHERE experiment_id=?", (experiment_id,)).fetchone()
            if exists is None:
                raise TaskNotFound("experiment not found")
            rows = c.execute(
                "SELECT task_id,assignment,assignment_key,assigned_at FROM workforce_experiment_assignments WHERE experiment_id=? ORDER BY assigned_at,task_id LIMIT ?",
                (experiment_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_workforce_experiment_delivery(
        self, experiment_id, task_id, delivery_state, *, actor_id,
        intervention_version=None, evidence_reference=None,
    ):
        """Record an operator attestation; this method does not execute treatment."""
        for name, value in (("experiment_id", experiment_id), ("task_id", task_id),
                            ("actor_id", actor_id)):
            if not isinstance(value, str) or not 1 <= len(value) <= 256 or value.strip() != value:
                raise ValueError("invalid " + name)
        if delivery_state not in {"delivered", "not_delivered", "unknown"}:
            raise ValueError("invalid delivery_state")
        if delivery_state == "delivered":
            if (not isinstance(intervention_version, str) or not intervention_version.strip()
                    or len(intervention_version) > 128):
                raise ValueError("delivered status requires intervention_version")
        elif intervention_version is not None:
            raise ValueError("intervention_version is only valid when delivered")
        if evidence_reference is not None and (
            not isinstance(evidence_reference, str) or not evidence_reference.strip()
            or len(evidence_reference) > 512
        ):
            raise ValueError("invalid evidence_reference")
        now = self.clock()
        with closing(self._connect()) as c:
            c.execute("BEGIN IMMEDIATE")
            assignment = c.execute(
                """SELECT a.assignment,e.state,e.design_json
                   FROM workforce_experiment_assignments a
                   JOIN workforce_experiments e ON e.experiment_id=a.experiment_id
                   WHERE a.experiment_id=? AND a.task_id=?""",
                (experiment_id, task_id),
            ).fetchone()
            if assignment is None:
                raise TaskNotFound("experiment assignment not found")
            if assignment["state"] != "approved":
                raise TaskConflict("experiment must remain approved to record delivery")
            if assignment["assignment"] != "treatment":
                raise TaskConflict("delivery attestations are only valid for treatment assignments")
            existing = c.execute(
                "SELECT * FROM workforce_experiment_deliveries WHERE experiment_id=? AND task_id=?",
                (experiment_id, task_id),
            ).fetchone()
            requested = (delivery_state, intervention_version, evidence_reference, actor_id)
            if existing is not None:
                original = (existing["delivery_state"], existing["intervention_version"],
                            existing["evidence_reference"], existing["actor_id"])
                if original != requested:
                    raise TaskConflict("delivery attestation already recorded with different values")
                c.commit()
                return {"created": False, "experiment_id": experiment_id, "task_id": task_id,
                        "assignment": "treatment", "delivery_state": delivery_state,
                        "intervention_version": intervention_version,
                        "evidence_reference": evidence_reference, "actor_id": actor_id,
                        "attested_at": existing["created_at"], "treatment_executed": False}
            c.execute(
                """INSERT INTO workforce_experiment_deliveries
                   (experiment_id,task_id,delivery_state,intervention_version,
                    evidence_reference,actor_id,created_at) VALUES(?,?,?,?,?,?,?)""",
                (experiment_id, task_id, delivery_state, intervention_version,
                 evidence_reference, actor_id, now),
            )
            self._event(c, c.execute("SELECT goal_id FROM tasks WHERE id=?", (task_id,)).fetchone()["goal_id"],
                        task_id, "experiment.delivery_attested", {
                            "experiment_id": experiment_id, "delivery_state": delivery_state,
                            "actor_id": actor_id,
                        })
            c.commit()
        return {"created": True, "experiment_id": experiment_id, "task_id": task_id,
                "assignment": "treatment", "delivery_state": delivery_state,
                "intervention_version": intervention_version,
                "evidence_reference": evidence_reference, "actor_id": actor_id,
                "attested_at": now, "treatment_executed": False}

    def list_workforce_experiment_deliveries(self, experiment_id, limit=500):
        if not isinstance(experiment_id, str) or not 1 <= len(experiment_id) <= 256:
            raise ValueError("invalid experiment_id")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 500:
            raise ValueError("limit must be 1..500")
        with closing(self._connect()) as c:
            if c.execute("SELECT 1 FROM workforce_experiments WHERE experiment_id=?",
                         (experiment_id,)).fetchone() is None:
                raise TaskNotFound("experiment not found")
            rows = c.execute(
                """SELECT task_id,delivery_state,intervention_version,evidence_reference,
                          actor_id,created_at AS attested_at
                   FROM workforce_experiment_deliveries WHERE experiment_id=?
                   ORDER BY created_at,task_id LIMIT ?""",
                (experiment_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_workforce_experiment_execution_event(
        self, experiment_id, task_id, event_type, *, worker_id, attempt,
    ):
        """Append trusted worker lifecycle telemetry for an assigned task."""
        for name, value in (("experiment_id", experiment_id), ("task_id", task_id),
                            ("worker_id", worker_id)):
            if not isinstance(value, str) or not 1 <= len(value) <= 256 or value.strip() != value:
                raise ValueError("invalid " + name)
        if event_type not in {"execution_started", "execution_succeeded", "execution_failed"}:
            raise ValueError("invalid execution event type")
        if type(attempt) is not int or attempt < 1:
            raise ValueError("attempt must be a positive integer")
        now = self.clock()
        with closing(self._connect()) as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute(
                """SELECT a.assignment,t.state,t.lease_owner,t.lease_until,t.attempts,e.state AS experiment_state
                   FROM workforce_experiment_assignments a
                   JOIN tasks t ON t.id=a.task_id
                   JOIN workforce_experiments e ON e.experiment_id=a.experiment_id
                   WHERE a.experiment_id=? AND a.task_id=?""",
                (experiment_id, task_id),
            ).fetchone()
            if row is None:
                raise TaskNotFound("experiment assignment not found")
            if row["experiment_state"] != "approved":
                raise TaskConflict("experiment must remain approved during execution")
            if (row["state"] != "running" or row["lease_owner"] != worker_id
                    or row["lease_until"] is None or row["lease_until"] <= now
                    or row["attempts"] != attempt):
                raise TaskConflict("worker does not hold the matching live task lease")
            if event_type == "execution_started":
                prior = c.execute(
                    """SELECT 1 FROM workforce_experiment_execution_events
                       WHERE experiment_id=? AND task_id=? AND attempt=?
                         AND event_type='execution_started'""",
                    (experiment_id, task_id, attempt),
                ).fetchone()
                if prior:
                    c.commit()
                    return {"created": False, "experiment_id": experiment_id,
                            "task_id": task_id, "event_type": event_type,
                            "attempt": attempt, "worker_id": worker_id}
            else:
                started = c.execute(
                    """SELECT 1 FROM workforce_experiment_execution_events
                       WHERE experiment_id=? AND task_id=? AND attempt=?
                         AND event_type='execution_started'""",
                    (experiment_id, task_id, attempt),
                ).fetchone()
                if not started:
                    raise TaskConflict("execution terminal event requires a recorded start")
                terminal = c.execute(
                    """SELECT 1 FROM workforce_experiment_execution_events
                       WHERE experiment_id=? AND task_id=? AND attempt=?
                         AND event_type IN ('execution_succeeded','execution_failed')""",
                    (experiment_id, task_id, attempt),
                ).fetchone()
                if terminal:
                    raise TaskConflict("execution attempt already has a terminal event")
            try:
                c.execute(
                    """INSERT INTO workforce_experiment_execution_events
                       (experiment_id,task_id,event_type,attempt,worker_id,created_at)
                       VALUES(?,?,?,?,?,?)""",
                    (experiment_id, task_id, event_type, attempt, worker_id, now),
                )
            except sqlite3.IntegrityError as exc:
                raise TaskConflict("execution event already recorded") from exc
            c.execute(
                """INSERT INTO workforce_experiment_events
                   (experiment_id,event_type,actor_id,detail_json,created_at)
                   VALUES(?,?,?,?,?)""",
                (experiment_id, "experiment." + event_type, worker_id,
                 self._json({"task_id": task_id, "attempt": attempt}), now),
            )
            c.commit()
        return {"created": True, "experiment_id": experiment_id, "task_id": task_id,
                "event_type": event_type, "attempt": attempt, "worker_id": worker_id}

    def get_task_workforce_experiment_context(self, task_id):
        """Return approved experiment assignment context for trusted internal workers."""
        if not isinstance(task_id, str) or not 1 <= len(task_id) <= 256:
            raise ValueError("invalid task_id")
        with closing(self._connect()) as c:
            row = c.execute(
                """SELECT a.experiment_id,a.assignment,e.state,e.design_json
                   FROM workforce_experiment_assignments a
                   JOIN workforce_experiments e ON e.experiment_id=a.experiment_id
                   WHERE a.task_id=?""",
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        design = json.loads(row["design_json"])
        intervention = design.get("intervention")
        version = hashlib.sha256(intervention.encode("utf-8")).hexdigest() if isinstance(intervention, str) else None
        return {
            "experiment_id": row["experiment_id"],
            "assignment": row["assignment"],
            "experiment_state": row["state"],
            "intervention": intervention if row["assignment"] == "treatment" else None,
            "intervention_version": version if row["assignment"] == "treatment" else None,
        }

    def record_workforce_experiment_treatment_exposure(
        self, experiment_id, task_id, *, worker_id, attempt, intervention_version, skill_name,
    ):
        """Record treatment only at the trusted adapter immediately before skill invocation."""
        for name, value in (("experiment_id", experiment_id), ("task_id", task_id),
                            ("worker_id", worker_id), ("intervention_version", intervention_version),
                            ("skill_name", skill_name)):
            if not isinstance(value, str) or not 1 <= len(value) <= 256 or value.strip() != value:
                raise ValueError("invalid " + name)
        if type(attempt) is not int or attempt < 1:
            raise ValueError("attempt must be a positive integer")
        now = self.clock()
        with closing(self._connect()) as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute(
                """SELECT a.assignment,e.state,t.state AS task_state,t.lease_owner,
                          t.lease_until,t.attempts,e.design_json
                   FROM workforce_experiment_assignments a
                   JOIN workforce_experiments e ON e.experiment_id=a.experiment_id
                   JOIN tasks t ON t.id=a.task_id
                   WHERE a.experiment_id=? AND a.task_id=?""",
                (experiment_id, task_id),
            ).fetchone()
            if row is None:
                raise TaskNotFound("experiment assignment not found")
            if row["assignment"] != "treatment":
                raise TaskConflict("control assignments cannot record treatment exposure")
            if row["state"] != "approved" or row["task_state"] != "running":
                raise TaskConflict("approved experiment and running task are required")
            if (row["lease_owner"] != worker_id or row["lease_until"] is None
                    or row["lease_until"] <= now or row["attempts"] != attempt):
                raise TaskConflict("worker does not hold the matching live task lease")
            intervention = json.loads(row["design_json"]).get("intervention")
            expected_version = hashlib.sha256(intervention.encode("utf-8")).hexdigest()
            if intervention_version != expected_version:
                raise TaskConflict("intervention version does not match the approved experiment design")
            existing = c.execute(
                """SELECT worker_id,intervention_version,skill_name FROM workforce_experiment_treatment_exposures
                   WHERE experiment_id=? AND task_id=? AND attempt=?""",
                (experiment_id, task_id, attempt),
            ).fetchone()
            requested = (worker_id, intervention_version, skill_name)
            if existing is not None:
                if (existing["worker_id"], existing["intervention_version"], existing["skill_name"]) != requested:
                    raise TaskConflict("treatment exposure already recorded with different provenance")
                c.commit()
                return {"created": False, "experiment_id": experiment_id, "task_id": task_id,
                        "attempt": attempt, "intervention_version": intervention_version,
                        "skill_name": skill_name}
            c.execute(
                """INSERT INTO workforce_experiment_treatment_exposures
                   (experiment_id,task_id,attempt,worker_id,intervention_version,skill_name,created_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (experiment_id, task_id, attempt, worker_id, intervention_version, skill_name, now),
            )
            c.execute(
                """INSERT INTO workforce_experiment_events
                   (experiment_id,event_type,actor_id,detail_json,created_at)
                   VALUES(?,?,?,?,?)""",
                (experiment_id, "experiment.treatment_exposed", worker_id,
                 self._json({"task_id": task_id, "attempt": attempt,
                             "intervention_version": intervention_version, "skill_name": skill_name}), now),
            )
            c.commit()
        return {"created": True, "experiment_id": experiment_id, "task_id": task_id,
                "attempt": attempt, "intervention_version": intervention_version,
                "skill_name": skill_name}

    def list_workforce_experiment_treatment_exposures(self, experiment_id, *, limit=500):
        if not isinstance(experiment_id, str) or not 1 <= len(experiment_id) <= 256:
            raise ValueError("invalid experiment_id")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("limit must be an integer between 1 and 500")
        with closing(self._connect()) as c:
            if c.execute("SELECT 1 FROM workforce_experiments WHERE experiment_id=?",
                         (experiment_id,)).fetchone() is None:
                raise TaskNotFound("experiment not found")
            rows = c.execute(
                """SELECT task_id,attempt,worker_id,intervention_version,skill_name,created_at
                   FROM workforce_experiment_treatment_exposures
                   WHERE experiment_id=? ORDER BY exposure_id LIMIT ?""",
                (experiment_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_task_experiment_execution_event(self, task_id, event_type, *, worker_id, attempt):
        """Record an execution event when a task belongs to an experiment; otherwise no-op."""
        if not isinstance(task_id, str) or not 1 <= len(task_id) <= 256:
            raise ValueError("invalid task_id")
        with closing(self._connect()) as c:
            row = c.execute(
                "SELECT experiment_id FROM workforce_experiment_assignments WHERE task_id=?",
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        return self.record_workforce_experiment_execution_event(
            row["experiment_id"], task_id, event_type,
            worker_id=worker_id, attempt=attempt,
        )

    def list_workforce_experiment_execution_events(self, experiment_id, *, limit=500):
        if not isinstance(experiment_id, str) or not 1 <= len(experiment_id) <= 256:
            raise ValueError("invalid experiment_id")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("limit must be an integer between 1 and 500")
        with closing(self._connect()) as c:
            if c.execute("SELECT 1 FROM workforce_experiments WHERE experiment_id=?",
                         (experiment_id,)).fetchone() is None:
                raise TaskNotFound("experiment not found")
            rows = c.execute(
                """SELECT task_id,event_type,attempt,worker_id,created_at
                   FROM workforce_experiment_execution_events
                   WHERE experiment_id=? ORDER BY event_id LIMIT ?""",
                (experiment_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_workforce_experiment_integrity(self, experiment_id):
        """Audit assignment, delivery, worker execution, and treatment invocation evidence."""
        if not isinstance(experiment_id, str) or not 1 <= len(experiment_id) <= 256:
            raise ValueError("invalid experiment_id")
        with closing(self._connect()) as c:
            experiment = c.execute(
                "SELECT design_json,state FROM workforce_experiments WHERE experiment_id=?",
                (experiment_id,),
            ).fetchone()
            if experiment is None:
                raise TaskNotFound("experiment not found")
            rows = c.execute(
                """SELECT a.task_id,a.assignment,a.assignment_key,a.assigned_at,
                          t.task_type,d.delivery_state,d.intervention_version
                   FROM workforce_experiment_assignments a
                   JOIN tasks t ON t.id=a.task_id
                   LEFT JOIN workforce_experiment_deliveries d
                     ON d.experiment_id=a.experiment_id AND d.task_id=a.task_id
                   WHERE a.experiment_id=? ORDER BY a.task_id""",
                (experiment_id,),
            ).fetchall()
            duplicate_rows = c.execute(
                """SELECT task_id,COUNT(*) AS assignment_count
                   FROM workforce_experiment_assignments
                   WHERE task_id IN (
                     SELECT task_id FROM workforce_experiment_assignments WHERE experiment_id=?
                   )
                   GROUP BY task_id HAVING COUNT(*)>1""",
                (experiment_id,),
            ).fetchall()
            control_delivery_count = c.execute(
                """SELECT COUNT(*) AS n FROM workforce_experiment_deliveries d
                   JOIN workforce_experiment_assignments a
                     ON a.experiment_id=d.experiment_id AND a.task_id=d.task_id
                   WHERE d.experiment_id=? AND a.assignment!='treatment'""",
                (experiment_id,),
            ).fetchone()["n"]
            execution_rows = c.execute(
                """SELECT event_type,COUNT(*) AS event_count FROM workforce_experiment_execution_events
                   WHERE experiment_id=? GROUP BY event_type""",
                (experiment_id,),
            ).fetchall()
            repeated_execution_rows = c.execute(
                """SELECT task_id,COUNT(DISTINCT attempt) AS attempts
                   FROM workforce_experiment_execution_events
                   WHERE experiment_id=? AND event_type='execution_started'
                   GROUP BY task_id HAVING COUNT(DISTINCT attempt)>1""",
                (experiment_id,),
            ).fetchall()
            incomplete_execution_rows = c.execute(
                """SELECT started.task_id,started.attempt
                   FROM workforce_experiment_execution_events started
                   WHERE started.experiment_id=? AND started.event_type='execution_started'
                     AND NOT EXISTS (
                       SELECT 1 FROM workforce_experiment_execution_events terminal
                       WHERE terminal.experiment_id=started.experiment_id
                         AND terminal.task_id=started.task_id
                         AND terminal.attempt=started.attempt
                         AND terminal.event_type IN ('execution_succeeded','execution_failed')
                     )""",
                (experiment_id,),
            ).fetchall()
            exposure_rows = c.execute(
                """SELECT task_id,attempt,intervention_version,skill_name
                   FROM workforce_experiment_treatment_exposures
                   WHERE experiment_id=? ORDER BY exposure_id""",
                (experiment_id,),
            ).fetchall()
            repeated_exposure_rows = c.execute(
                """SELECT task_id,COUNT(DISTINCT attempt) AS attempts
                   FROM workforce_experiment_treatment_exposures
                   WHERE experiment_id=? GROUP BY task_id
                   HAVING COUNT(DISTINCT attempt)>1""",
                (experiment_id,),
            ).fetchall()
            control_exposure_count = c.execute(
                """SELECT COUNT(*) AS n FROM workforce_experiment_treatment_exposures x
                   JOIN workforce_experiment_assignments a
                     ON a.experiment_id=x.experiment_id AND a.task_id=x.task_id
                   WHERE x.experiment_id=? AND a.assignment!='treatment'""",
                (experiment_id,),
            ).fetchone()["n"]
            treatment_started_rows = c.execute(
                """SELECT a.task_id,e.attempt FROM workforce_experiment_assignments a
                   JOIN workforce_experiment_execution_events e
                     ON e.experiment_id=a.experiment_id AND e.task_id=a.task_id
                        AND e.event_type='execution_started'
                   WHERE a.experiment_id=? AND a.assignment='treatment'""",
                (experiment_id,),
            ).fetchall()
        design = json.loads(experiment["design_json"])
        population = design.get("eligible_population")
        allowed_types = (
            {item.casefold() for item in population.get("task_types", [])}
            if isinstance(population, dict) and isinstance(population.get("task_types"), list)
            else None
        )
        issues = []
        execution_counts = {"execution_started": 0, "execution_succeeded": 0, "execution_failed": 0}
        for row in execution_rows:
            if row["event_type"] in execution_counts:
                execution_counts[row["event_type"]] = row["event_count"]
        for row in repeated_execution_rows:
            issues.append({"code": "repeated_worker_execution", "task_id": row["task_id"],
                           "attempts": row["attempts"]})
        for row in incomplete_execution_rows:
            issues.append({"code": "worker_execution_missing_terminal_event",
                           "task_id": row["task_id"], "attempt": row["attempt"]})
        exposure_task_attempts = {(row["task_id"], row["attempt"]) for row in exposure_rows}
        for row in exposure_rows:
            expected_version = hashlib.sha256(
                json.loads(experiment["design_json"])["intervention"].encode("utf-8")
            ).hexdigest()
            if row["intervention_version"] != expected_version:
                issues.append({"code": "treatment_intervention_version_mismatch",
                               "task_id": row["task_id"], "attempt": row["attempt"]})
        for row in repeated_exposure_rows:
            issues.append({"code": "repeated_treatment_exposure",
                           "task_id": row["task_id"], "attempts": row["attempts"]})
        if control_exposure_count:
            issues.append({"code": "control_arm_has_treatment_exposure",
                           "count": control_exposure_count})
        for row in treatment_started_rows:
            if (row["task_id"], row["attempt"]) not in exposure_task_attempts:
                issues.append({"code": "treatment_execution_without_skill_exposure",
                               "task_id": row["task_id"], "attempt": row["attempt"]})
        delivery_counts = {"delivered": 0, "not_delivered": 0, "unknown": 0, "unrecorded": 0}
        for row in rows:
            expected_key = hashlib.sha256(
                (experiment_id + "\\0" + row["task_id"]).encode("utf-8")
            ).hexdigest()
            expected_arm = "treatment" if int(expected_key[:8], 16) % 2 else "control"
            if row["assignment_key"] != expected_key or row["assignment"] != expected_arm:
                issues.append({"code": "assignment_integrity_mismatch", "task_id": row["task_id"]})
            if allowed_types is not None and row["task_type"].casefold() not in allowed_types:
                issues.append({"code": "eligibility_drift", "task_id": row["task_id"]})
            if row["assignment"] == "treatment":
                state = row["delivery_state"] or "unrecorded"
                delivery_counts[state] = delivery_counts.get(state, 0) + 1
            elif row["delivery_state"] is not None:
                issues.append({"code": "control_arm_has_treatment_delivery", "task_id": row["task_id"]})
            if row["delivery_state"] == "delivered" and not row["intervention_version"]:
                issues.append({"code": "delivered_without_intervention_version", "task_id": row["task_id"]})
        for row in duplicate_rows:
            issues.append({"code": "multiple_experiment_assignments", "task_id": row["task_id"],
                           "assignment_count": row["assignment_count"]})
        if control_delivery_count:
            issues.append({"code": "control_arm_delivery_records_present",
                           "count": control_delivery_count})
        if delivery_counts["unrecorded"]:
            issues.append({"code": "treatment_delivery_unrecorded",
                           "count": delivery_counts["unrecorded"]})
        return {
            "experiment_id": experiment_id,
            "experiment_state": experiment["state"],
            "assignments_checked": len(rows),
            "treatment_delivery_attestations": delivery_counts,
            "worker_execution_events": execution_counts,
            "treatment_exposures": len(exposure_rows),
            "treatment_exposure_skills": sorted({row["skill_name"] for row in exposure_rows}),
            "issues": issues,
            "issue_count": len(issues),
            "status": "integrity_warnings" if issues else "no_detected_integrity_issues",
            "scope": [
                "Persisted assignment keys and deterministic arm consistency",
                "Current task-type eligibility against the stored design",
                "Treatment delivery attestation coverage and arm consistency",
                "Worker execution lifecycle events emitted by the trusted workforce worker",
                "Treatment content passed to the selected skill immediately before its execute() invocation",
                "Repeated execution attempts visible in the execution event stream",
                "Duplicate task assignment rows visible to this database query",
            ],
            "limitations": [
                "Database uniqueness prevents normal cross-experiment task reassignment; rejected attempts are not retained as contamination events.",
                "Worker execution events prove that the workforce worker entered or exited its executor seam, not that a specific treatment was delivered.",
                "Treatment exposure means the approved intervention text was appended to the selected skill argument, all final invocation checks passed, and the skill execute() boundary was reached; it does not prove the model attended to, followed, or benefited from the content.",
                "Operator delivery attestations are not proof of execution.",
                "A clean report is not proof that no contamination occurred.",
            ],
            "automatic_action": False,
        }

    def get_workforce_experiment_outcomes(self, experiment_id):
        """Return read-only lifecycle outcomes for assigned tasks, grouped by arm.

        Task state is the source of truth; callers cannot submit outcome claims.
        Retried/non-terminal tasks remain pending. Cancellation is reported
        separately and excluded from the succeeded/failed success-rate denominator.
        """
        if not isinstance(experiment_id, str) or not 1 <= len(experiment_id) <= 256:
            raise ValueError("invalid experiment_id")
        with closing(self._connect()) as c:
            experiment = c.execute(
                "SELECT design_json,state FROM workforce_experiments WHERE experiment_id=?",
                (experiment_id,),
            ).fetchone()
            if experiment is None:
                raise TaskNotFound("experiment not found")
            rows = c.execute(
                """SELECT a.assignment, t.state, COUNT(*) AS task_count
                   FROM workforce_experiment_assignments a
                   JOIN tasks t ON t.id=a.task_id
                   WHERE a.experiment_id=?
                   GROUP BY a.assignment,t.state""",
                (experiment_id,),
            ).fetchall()
            delivery_rows = c.execute(
                """SELECT d.delivery_state, COUNT(*) AS task_count
                   FROM workforce_experiment_assignments a
                   LEFT JOIN workforce_experiment_deliveries d
                     ON d.experiment_id=a.experiment_id AND d.task_id=a.task_id
                   WHERE a.experiment_id=? AND a.assignment='treatment'
                   GROUP BY d.delivery_state""",
                (experiment_id,),
            ).fetchall()
            exposure_rows = c.execute(
                """SELECT COUNT(DISTINCT task_id) AS exposed_tasks,
                          COUNT(*) AS exposure_attempts
                   FROM workforce_experiment_treatment_exposures
                   WHERE experiment_id=?""",
                (experiment_id,),
            ).fetchone()
            exposure_skill_rows = c.execute(
                """SELECT skill_name,COUNT(*) AS attempts,
                          COUNT(DISTINCT task_id) AS tasks
                   FROM workforce_experiment_treatment_exposures
                   WHERE experiment_id=? GROUP BY skill_name ORDER BY skill_name""",
                (experiment_id,),
            ).fetchall()
        arms = {}
        for arm in ("control", "treatment"):
            counts = {"succeeded": 0, "failed": 0, "cancelled": 0, "pending": 0}
            for row in rows:
                if row["assignment"] != arm:
                    continue
                state = row["state"]
                bucket = state if state in {"succeeded", "failed", "cancelled"} else "pending"
                counts[bucket] += row["task_count"]
            terminal_evaluable = counts["succeeded"] + counts["failed"]
            total = sum(counts.values())
            arms[arm] = {
                "assigned": total,
                **counts,
                "evaluable": terminal_evaluable,
                "success_rate": (
                    counts["succeeded"] / terminal_evaluable
                    if terminal_evaluable else None
                ),
            }
        delivery = {"delivered": 0, "not_delivered": 0, "unknown": 0, "unrecorded": 0}
        for row in delivery_rows:
            key = row["delivery_state"] if row["delivery_state"] in delivery else "unrecorded"
            delivery[key] += row["task_count"]
        treatment_assigned = arms["treatment"]["assigned"]
        delivery["unrecorded"] = treatment_assigned - sum(
            delivery[key] for key in ("delivered", "not_delivered", "unknown")
        )
        arms["treatment"]["delivery_attestations"] = delivery
        treatment_exposed_tasks = exposure_rows["exposed_tasks"] or 0
        treatment_exposure_attempts = exposure_rows["exposure_attempts"] or 0
        treatment_assigned_count = arms["treatment"]["assigned"]
        treatment_exposure_coverage = (
            treatment_exposed_tasks / treatment_assigned_count
            if treatment_assigned_count else None
        )
        arms["treatment"]["runtime_exposure"] = {
            "exposed_tasks": treatment_exposed_tasks,
            "unexposed_assigned_tasks": max(0, treatment_assigned_count - treatment_exposed_tasks),
            "exposure_attempts": treatment_exposure_attempts,
            "task_coverage": treatment_exposure_coverage,
            "skills": [dict(row) for row in exposure_skill_rows],
        }
        design = json.loads(experiment["design_json"])
        minimum = design.get("minimum_sample_size")
        evaluable_total = sum(arm["evaluable"] for arm in arms.values())
        enough_observations = (
            isinstance(minimum, int) and not isinstance(minimum, bool)
            and evaluable_total >= minimum
        )
        control_rate = arms["control"]["success_rate"]
        treatment_rate = arms["treatment"]["success_rate"]
        comparison_available = (
            enough_observations and control_rate is not None and treatment_rate is not None
        )
        success_rate_difference = (
            treatment_rate - control_rate if comparison_available else None
        )
        return {
            "experiment_id": experiment_id,
            "experiment_state": experiment["state"],
            "design_type": design.get("design_type"),
            "primary_metric": design.get("primary_metric"),
            "minimum_sample_size": minimum,
            "arms": arms,
            "descriptive_comparison": {
                "status": "descriptive_comparison_available" if comparison_available
                          else "insufficient_arm_observations",
                "control_success_rate": control_rate,
                "treatment_success_rate": treatment_rate,
                "success_rate_difference_treatment_minus_control": success_rate_difference,
                "direction": (
                    "treatment_higher" if success_rate_difference is not None and success_rate_difference > 0
                    else "treatment_lower" if success_rate_difference is not None and success_rate_difference < 0
                    else "equal" if success_rate_difference == 0
                    else "not_available"
                ),
                "minimum_sample_size_met": enough_observations,
                "both_arms_have_evaluable_tasks": control_rate is not None and treatment_rate is not None,
                "causal_inference": False,
                "statistical_significance_tested": False,
            },
            "total_assigned": sum(arm["assigned"] for arm in arms.values()),
            "total_evaluable": evaluable_total,
            "status": "descriptive_outcomes_available" if enough_observations
                      else "insufficient_observations",
            "interpretation": (
                "Per-arm success rates and their difference are descriptive only. "
                "No causal effect, statistical significance, causal validity, "
                "eligibility, or absence of contamination is established. Runtime "
                "exposure records mean approved treatment text reached the guarded "
                "skill execute boundary; they do not prove model attention, compliance, "
                "or benefit. Delivery fields are operator attestations, not proof of "
                "execution. Cancelled and pending tasks are excluded from success-rate "
                "denominators. Task-mix, timing, retries, and operational differences "
                "may explain observed arm differences."
            ),
            "automatic_action": False,
        }

    def get_task_by_idempotency(self, goal_id, idempotency_key):
        """Return a task and its persisted dependency IDs for safe DAG recovery."""
        if not isinstance(goal_id, str) or not goal_id.strip():
            raise ValueError("goal_id must be non-empty text")
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 256:
            raise ValueError("idempotency_key must contain 1..256 characters")
        with closing(self._connect()) as c:
            row = c.execute(
                "SELECT * FROM tasks WHERE goal_id=? AND idempotency_key=?",
                (goal_id, idempotency_key),
            ).fetchone()
            if row is None:
                return None
            task = self._decode(row)
            dependencies = c.execute(
                "SELECT depends_on FROM dependencies WHERE task_id=? ORDER BY depends_on",
                (task["id"],),
            ).fetchall()
        task["dependencies"] = [item["depends_on"] for item in dependencies]
        return task

    def claim(self, worker_id, *, lease_seconds=60, task_type=None):
        if not isinstance(worker_id,str) or not 1 <= len(worker_id) <= 256: raise ValueError("invalid worker_id")
        if task_type is not None and (not isinstance(task_type, str) or not 1 <= len(task_type) <= 128): raise ValueError("invalid task_type")
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or not 1 <= lease_seconds <= 3600: raise ValueError("lease_seconds must be an integer in 1..3600")
        now=self.clock()
        with closing(self._connect()) as c:
            c.execute("BEGIN IMMEDIATE")
            row=c.execute("""SELECT t.* FROM tasks t JOIN goals g ON g.id=t.goal_id
              WHERE t.state='queued' AND t.available_at<=? AND g.state='active'
              AND (? IS NULL OR t.task_type=?)
              AND NOT EXISTS(SELECT 1 FROM dependencies d JOIN tasks p ON p.id=d.depends_on
                WHERE d.task_id=t.id AND p.state<>'succeeded')
              ORDER BY t.available_at,t.created_at,t.id LIMIT 1""",(now,task_type,task_type)).fetchone()
            if row is None: c.commit(); return None
            until=now+lease_seconds
            changed=c.execute("UPDATE tasks SET state='running',lease_owner=?,lease_until=?,attempts=attempts+1,updated_at=? WHERE id=? AND state='queued'",
                              (worker_id,until,now,row["id"])).rowcount
            if changed!=1: c.rollback(); return None
            self._event(c,row["goal_id"],row["id"],"task.claimed",{"worker_id":worker_id,"attempt":row["attempts"]+1,"lease_until":until})
            c.commit()
        return self.get_task(row["id"])

    def _owned(self,c,task_id,worker_id):
        r=c.execute("SELECT * FROM tasks WHERE id=?",(task_id,)).fetchone()
        if r is None: raise TaskNotFound("task not found")
        if r["state"]!="running" or r["lease_owner"]!=worker_id or r["lease_until"] is None or r["lease_until"]<=self.clock():
            raise TaskConflict("worker does not hold a live lease")
        return r

    def heartbeat(self,task_id,worker_id,*,lease_seconds=60):
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or not 1<=lease_seconds<=3600: raise ValueError("lease_seconds must be an integer in 1..3600")
        now=self.clock()
        with closing(self._connect()) as c:
            c.execute("BEGIN IMMEDIATE"); r=self._owned(c,task_id,worker_id); until=now+lease_seconds
            c.execute("UPDATE tasks SET lease_until=?,updated_at=? WHERE id=?",(until,now,task_id))
            self._event(c,r["goal_id"],task_id,"task.heartbeat",{"worker_id":worker_id,"lease_until":until}); c.commit()
        return until

    def complete(self,task_id,worker_id,result=None):
        result_json,now=self._json(result),self.clock()
        with closing(self._connect()) as c:
            c.execute("BEGIN IMMEDIATE"); r=self._owned(c,task_id,worker_id)
            c.execute("UPDATE tasks SET state='succeeded',result_json=?,lease_owner=NULL,lease_until=NULL,updated_at=? WHERE id=?",(result_json,now,task_id))
            self._event(c,r["goal_id"],task_id,"task.succeeded",{"attempt":r["attempts"]})
            self._complete_goal_if_terminal(c,r["goal_id"],now); c.commit()
        return self.get_task(task_id)

    def fail(self,task_id,worker_id,error,*,retry_delay_seconds=0):
        if not isinstance(error,str) or not error.strip(): raise ValueError("error must be non-empty")
        if isinstance(retry_delay_seconds, bool) or not isinstance(retry_delay_seconds, (int, float)) or not 0<=retry_delay_seconds<=86400 or not math.isfinite(retry_delay_seconds): raise ValueError("retry delay must be finite and 0..86400")
        # Worker exception text can contain credentials, prompts, or customer data.
        # Persist a stable safe marker rather than raw exception text.
        now,safe_error=self.clock(),"worker reported an error (details redacted)"
        with closing(self._connect()) as c:
            c.execute("BEGIN IMMEDIATE"); r=self._owned(c,task_id,worker_id)
            retry=r["attempts"]<r["max_attempts"]; state="queued" if retry else "failed"
            c.execute("UPDATE tasks SET state=?,available_at=?,last_error=?,lease_owner=NULL,lease_until=NULL,updated_at=? WHERE id=?",
                      (state,now+retry_delay_seconds,safe_error,now,task_id))
            self._event(c,r["goal_id"],task_id,"task.retry_queued" if retry else "task.failed",{"attempt":r["attempts"],"error":safe_error})
            if not retry:
                self._fail_blocked_descendants(c, r["goal_id"], now)
                self._complete_goal_if_terminal(c,r["goal_id"],now)
            c.commit()
        return self.get_task(task_id)

    def reject(self, task_id, worker_id, reason):
        """Terminally reject a claimed task without consuming retry attempts.

        Only stable reason codes are accepted; caller-controlled detail is
        never persisted in task state or event logs.
        """
        messages = {
            "unsupported_task_type": "task type is not supported by this worker",
            "unsafe_retry_policy": "side-effecting tasks must use a single attempt",
            "invalid_payload": "skill invocation payload is invalid",
        }
        if not isinstance(reason, str) or reason not in messages:
            raise ValueError("invalid task rejection reason")
        now = self.clock()
        safe_error = messages[reason]
        with closing(self._connect()) as c:
            c.execute("BEGIN IMMEDIATE")
            r = self._owned(c, task_id, worker_id)
            c.execute(
                """UPDATE tasks SET state='failed',last_error=?,lease_owner=NULL,
                   lease_until=NULL,updated_at=? WHERE id=?""",
                (safe_error, now, task_id),
            )
            self._event(c, r["goal_id"], task_id, "task.rejected", {"reason": reason})
            self._fail_blocked_descendants(c, r["goal_id"], now)
            self._complete_goal_if_terminal(c, r["goal_id"], now)
            c.commit()
        return self.get_task(task_id)

    def recover_expired_leases(self):
        now,count=self.clock(),0
        with closing(self._connect()) as c:
            c.execute("BEGIN IMMEDIATE")
            rows=c.execute("SELECT * FROM tasks WHERE state='running' AND lease_until<=?",(now,)).fetchall()
            for r in rows:
                retry=r["attempts"]<r["max_attempts"]; state="queued" if retry else "failed"
                c.execute("UPDATE tasks SET state=?,lease_owner=NULL,lease_until=NULL,last_error='worker lease expired',updated_at=? WHERE id=?",(state,now,r["id"]))
                self._event(c,r["goal_id"],r["id"],"task.lease_expired",{"attempt":r["attempts"],"retry":retry})
                if not retry:
                    self._fail_blocked_descendants(c, r["goal_id"], now)
                    self._complete_goal_if_terminal(c,r["goal_id"],now)
                count+=1
            c.commit()
        return count

    def cancel_task(self,task_id):
        now=self.clock()
        with closing(self._connect()) as c:
            c.execute("BEGIN IMMEDIATE"); r=c.execute("SELECT * FROM tasks WHERE id=?",(task_id,)).fetchone()
            if r is None: raise TaskNotFound("task not found")
            if r["state"] in TERMINAL: c.commit(); return self._decode(r)
            if r["state"] == "running":
                raise TaskConflict(
                    "running task cannot be cancelled safely; wait for the worker to finish"
                )
            c.execute("UPDATE tasks SET state='cancelled',lease_owner=NULL,lease_until=NULL,updated_at=? WHERE id=?",(now,task_id))
            self._event(c,r["goal_id"],task_id,"task.cancelled",{})
            self._fail_blocked_descendants(c, r["goal_id"], now)
            self._complete_goal_if_terminal(c,r["goal_id"],now)
            c.commit()
        return self.get_task(task_id)

    def _fail_blocked_descendants(self, c, goal_id, now):
        # A task cannot remain queued forever when a prerequisite is terminally unsuccessful.
        while True:
            rows = c.execute("""SELECT DISTINCT t.* FROM tasks t
                JOIN dependencies d ON d.task_id=t.id
                JOIN tasks p ON p.id=d.depends_on
                WHERE t.goal_id=? AND t.state='queued' AND p.state IN ('failed','cancelled')""",
                (goal_id,)).fetchall()
            if not rows:
                return
            for task in rows:
                c.execute("UPDATE tasks SET state='failed',last_error=?,updated_at=? WHERE id=? AND state='queued'",
                          ("dependency failed or was cancelled", now, task["id"]))
                self._event(c, goal_id, task["id"], "task.blocked_dependency", {"dependency": "terminal_unsuccessful"})
    
    def set_goal_state(self,goal_id,state):
        if not isinstance(state, str) or state not in {"active","paused","cancelled"}: raise ValueError("invalid goal state")
        now=self.clock()
        with closing(self._connect()) as c:
            c.execute("BEGIN IMMEDIATE"); g=c.execute("SELECT * FROM goals WHERE id=?",(goal_id,)).fetchone()
            if g is None: raise TaskNotFound("goal not found")
            if g["state"] == "completed": raise TaskConflict("completed goal cannot change state")
            if g["state"] == "cancelled" and state != "cancelled": raise TaskConflict("cancelled goal cannot be resumed")
            if state == "cancelled":
                running = c.execute(
                    "SELECT 1 FROM tasks WHERE goal_id=? AND state='running' LIMIT 1",
                    (goal_id,),
                ).fetchone()
                if running is not None:
                    raise TaskConflict(
                        "goal has running tasks; wait for them to finish before cancelling"
                    )
            c.execute("UPDATE goals SET state=?,updated_at=? WHERE id=?",(state,now,goal_id))
            cancelled_tasks = []
            if state=="cancelled":
                cancelled_tasks = c.execute(
                    "SELECT id FROM tasks WHERE goal_id=? AND state IN ('queued','running') ORDER BY created_at,id",
                    (goal_id,),
                ).fetchall()
                c.execute(
                    "UPDATE tasks SET state='cancelled',lease_owner=NULL,lease_until=NULL,updated_at=? WHERE goal_id=? AND state IN ('queued','running')",
                    (now,goal_id),
                )
                for task in cancelled_tasks:
                    self._event(c,goal_id,task["id"],"task.cancelled",{"reason":"goal_cancelled"})
                self._fail_blocked_descendants(c,goal_id,now)
            self._event(c,goal_id,None,"goal."+state,{"cancelled_task_count":len(cancelled_tasks)} if state=="cancelled" else {})
            # A task may have completed while the goal was paused. Re-evaluate
            # completion when resuming so terminal tasks cannot strand the goal.
            if state == "active":
                self._complete_goal_if_terminal(c, goal_id, now)
            c.commit()
        return self.get_goal(goal_id)

    def _complete_goal_if_terminal(self,c,goal_id,now):
        g=c.execute("SELECT state FROM goals WHERE id=?",(goal_id,)).fetchone()
        if g is None or g["state"]!="active": return
        pending=c.execute("SELECT 1 FROM tasks WHERE goal_id=? AND state NOT IN ('succeeded','failed','cancelled') LIMIT 1",(goal_id,)).fetchone()
        any_task=c.execute("SELECT 1 FROM tasks WHERE goal_id=? LIMIT 1",(goal_id,)).fetchone()
        if any_task and pending is None:
            c.execute("UPDATE goals SET state='completed',updated_at=? WHERE id=?",(now,goal_id)); self._event(c,goal_id,None,"goal.completed",{})

    def get_task(self,task_id):
        with closing(self._connect()) as c: r=c.execute("SELECT * FROM tasks WHERE id=?",(task_id,)).fetchone()
        if r is None: raise TaskNotFound("task not found")
        return self._decode(r)

    def get_goal(self,goal_id):
        with closing(self._connect()) as c: r=c.execute("SELECT * FROM goals WHERE id=?",(goal_id,)).fetchone()
        if r is None: raise TaskNotFound("goal not found")
        return dict(r)

    def list_goals(self, *, limit=100):
        """List the most recently updated goals for bounded dashboard reads."""
        if type(limit) is not int or not 1 <= limit <= 501:
            raise ValueError("limit must be an integer between 1 and 501")
        with closing(self._connect()) as c:
            rows = c.execute(
                "SELECT * FROM goals ORDER BY updated_at DESC,id LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_tasks(self,goal_id):
        with closing(self._connect()) as c: rows=c.execute("SELECT * FROM tasks WHERE goal_id=? ORDER BY created_at,id",(goal_id,)).fetchall()
        return [self._decode(r) for r in rows]

    def list_events(self,goal_id,*,after_id=0,limit=100):
        if not 1<=limit<=500: raise ValueError("limit must be 1..500")
        with closing(self._connect()) as c: rows=c.execute("SELECT * FROM run_events WHERE goal_id=? AND id>? ORDER BY id LIMIT ?",(goal_id,after_id,limit)).fetchall()
        return [{"id":r["id"],"goal_id":r["goal_id"],"task_id":r["task_id"],"event_type":r["event_type"],"detail":json.loads(r["detail_json"]),"created_at":r["created_at"]} for r in rows]
