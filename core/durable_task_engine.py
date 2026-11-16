"""Durable SQLite primitives for supervised agent task execution.

This module persists state but never executes task code or authorizes side effects.
"""
from __future__ import annotations
import json
import math
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
class RateLimitExceeded(TaskEngineError): pass

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
              created_at REAL NOT NULL, updated_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS goal_owners(
              goal_id TEXT PRIMARY KEY REFERENCES goals(id) ON DELETE CASCADE,\n              owner_id TEXT NOT NULL, created_at REAL NOT NULL);\n            CREATE TABLE IF NOT EXISTS tasks(
              id TEXT PRIMARY KEY, goal_id TEXT NOT NULL REFERENCES goals(id),
              task_type TEXT NOT NULL, payload_json TEXT NOT NULL, result_json TEXT,
              state TEXT NOT NULL CHECK(state IN ('queued','running','succeeded','failed','cancelled')),
              idempotency_key TEXT NOT NULL, submission_fingerprint TEXT, attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL,
              available_at REAL NOT NULL, lease_owner TEXT, lease_until REAL, last_error TEXT,
              created_at REAL NOT NULL, updated_at REAL NOT NULL, UNIQUE(goal_id,idempotency_key));
            CREATE TABLE IF NOT EXISTS submission_receipts(
              goal_id TEXT NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
              idempotency_key TEXT NOT NULL, actor_id TEXT NOT NULL,
              approval_id TEXT NOT NULL, task_fingerprint TEXT NOT NULL,
              task_id TEXT NOT NULL REFERENCES tasks(id), created_at REAL NOT NULL,
              PRIMARY KEY(goal_id,idempotency_key));
            CREATE TABLE IF NOT EXISTS rate_limit_events(
              id INTEGER PRIMARY KEY AUTOINCREMENT, actor_id TEXT NOT NULL, action TEXT NOT NULL, occurred_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS worker_instances(
              worker_id TEXT PRIMARY KEY, process_id INTEGER NOT NULL,
              started_at REAL NOT NULL, heartbeat_at REAL NOT NULL,
              state TEXT NOT NULL CHECK(state IN ('active','stopped')),
              stopped_at REAL);
            CREATE INDEX IF NOT EXISTS rate_limit_window_idx ON rate_limit_events(actor_id,action,occurred_at);
            CREATE INDEX IF NOT EXISTS tasks_claim_idx ON tasks(state,available_at,created_at);
            CREATE INDEX IF NOT EXISTS tasks_goal_idx ON tasks(goal_id,created_at);
            CREATE TABLE IF NOT EXISTS dependencies(
              task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
              depends_on TEXT NOT NULL REFERENCES tasks(id), PRIMARY KEY(task_id,depends_on), CHECK(task_id<>depends_on));
            CREATE TABLE IF NOT EXISTS run_events(
              id INTEGER PRIMARY KEY AUTOINCREMENT, goal_id TEXT NOT NULL, task_id TEXT,
              event_type TEXT NOT NULL, detail_json TEXT NOT NULL, created_at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS events_goal_idx ON run_events(goal_id,id);
            """)
            # Existing databases need an additive migration. Old rows have no
            # trustworthy fingerprint, so replaying them is rejected safely.
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

    def register_worker(self, worker_id, process_id):
        if not isinstance(worker_id, str) or not worker_id.strip() or len(worker_id) > 256:
            raise ValueError("invalid worker_id")
        if isinstance(process_id, bool) or not isinstance(process_id, int) or process_id <= 0:
            raise ValueError("invalid process_id")
        now = self.clock()
        with closing(self._connect()) as c:
            c.execute("BEGIN IMMEDIATE")
            existing = c.execute(
                "SELECT state FROM worker_instances WHERE worker_id=?", (worker_id,)
            ).fetchone()
            if existing is not None and existing["state"] == "active":
                raise TaskConflict("worker identity is already registered as active")
            c.execute(
                """INSERT INTO worker_instances(worker_id,process_id,started_at,heartbeat_at,state,stopped_at)
                   VALUES(?,?,?,?, 'active', NULL)
                   ON CONFLICT(worker_id) DO UPDATE SET
                     process_id=excluded.process_id, started_at=excluded.started_at,
                     heartbeat_at=excluded.heartbeat_at, state='active', stopped_at=NULL""",
                (worker_id, process_id, now, now),
            )
            c.commit()
        return self.get_worker(worker_id)

    def heartbeat_worker(self, worker_id):
        now = self.clock()
        with closing(self._connect()) as c:
            c.execute("BEGIN IMMEDIATE")
            changed = c.execute(
                "UPDATE worker_instances SET heartbeat_at=? WHERE worker_id=? AND state='active'",
                (now, worker_id),
            ).rowcount
            if changed != 1:
                raise TaskConflict("worker is not registered as active")
            c.commit()
        return now

    def stop_worker(self, worker_id):
        now = self.clock()
        with closing(self._connect()) as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute(
                "UPDATE worker_instances SET state='stopped',stopped_at=?,heartbeat_at=? "
                "WHERE worker_id=? AND state='active'",
                (now, now, worker_id),
            )
            c.commit()
        return self.get_worker(worker_id)

    def get_worker(self, worker_id):
        with closing(self._connect()) as c:
            row = c.execute(
                "SELECT * FROM worker_instances WHERE worker_id=?", (worker_id,)
            ).fetchone()
        if row is None:
            raise TaskNotFound("worker not found")
        return dict(row)

    def list_workers(self, *, stale_after_seconds=30):
        if (isinstance(stale_after_seconds, bool)
                or not isinstance(stale_after_seconds, (int, float))
                or not math.isfinite(stale_after_seconds)
                or not 1 <= stale_after_seconds <= 3600):
            raise ValueError("stale_after_seconds must be between 1 and 3600")
        now = self.clock()
        with closing(self._connect()) as c:
            rows = c.execute(
                "SELECT * FROM worker_instances ORDER BY started_at,worker_id"
            ).fetchall()
        return [
            {**dict(row), "stale": row["state"] == "active"
             and row["heartbeat_at"] <= now - stale_after_seconds}
            for row in rows
        ]

    def consume_rate_limit(self, actor_id, action, *, limit, window_seconds):
        """Atomically consume one persistent actor/action rate-limit slot."""
        if not isinstance(actor_id, str) or not actor_id.strip() or len(actor_id) > 200:
            raise ValueError("invalid rate-limit actor")
        if not isinstance(action, str) or not action or len(action) > 64:
            raise ValueError("invalid rate-limit action")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10000:
            raise ValueError("rate limit must be 1..10000")
        if isinstance(window_seconds, bool) or not isinstance(window_seconds, int) or not 1 <= window_seconds <= 86400:
            raise ValueError("rate-limit window must be 1..86400 seconds")
        now = self.clock()
        cutoff = now - window_seconds
        with closing(self._connect()) as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute(
                "DELETE FROM rate_limit_events WHERE actor_id=? AND action=? AND occurred_at<=?",
                (actor_id, action, cutoff),
            )
            count = c.execute(
                "SELECT COUNT(*) AS n FROM rate_limit_events WHERE actor_id=? AND action=? AND occurred_at>?",
                (actor_id, action, cutoff),
            ).fetchone()["n"]
            if count >= limit:
                c.commit()
                return False
            c.execute(
                "INSERT INTO rate_limit_events(actor_id,action,occurred_at) VALUES(?,?,?)",
                (actor_id, action, now),
            )
            c.commit()
        return True

    def create_goal(self, goal_id=None):
        goal_id, now = goal_id or str(uuid.uuid4()), self.clock()
        with closing(self._connect()) as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute("INSERT INTO goals VALUES(?,?,?,?)", (goal_id,"active",now,now))
            self._event(c,goal_id,None,"goal.created",{})
            c.commit()
        return goal_id

    def create_owned_goal(self, owner_id):
        """Create a goal and its immutable owner record in one SQLite transaction."""
        if not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 200:
            raise ValueError("owner_id must be bounded non-empty text")
        goal_id, now = str(uuid.uuid4()), self.clock()
        with closing(self._connect()) as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute("INSERT INTO goals VALUES(?,?,?,?)", (goal_id, "active", now, now))
            c.execute("INSERT INTO goal_owners(goal_id,owner_id,created_at) VALUES(?,?,?)",
                      (goal_id, owner_id, now))
            self._event(c, goal_id, None, "goal.created", {"owner_id": owner_id})
            c.commit()
        return goal_id

    def goal_owner(self, goal_id):
        with closing(self._connect()) as c:
            row = c.execute("SELECT owner_id FROM goal_owners WHERE goal_id=?", (goal_id,)).fetchone()
        return None if row is None else row["owner_id"]

    def submit(self, goal_id, task_type, payload, *, idempotency_key, dependencies=(), max_attempts=3, delay_seconds=0, submission_receipt=None, owner_id=None, max_active_tasks=None, rate_limit=None):
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
        rate_limit_spec = None
        if rate_limit is not None:
            if not isinstance(rate_limit, (tuple, list)) or len(rate_limit) != 4:
                raise ValueError("rate_limit must be (actor_id, action, limit, window_seconds)")
            rate_actor, rate_action, rate_count, rate_window = rate_limit
            if (not isinstance(rate_actor, str) or not rate_actor.strip() or len(rate_actor) > 200
                    or not isinstance(rate_action, str) or not rate_action or len(rate_action) > 64
                    or isinstance(rate_count, bool) or not isinstance(rate_count, int) or not 1 <= rate_count <= 10000
                    or isinstance(rate_window, bool) or not isinstance(rate_window, int) or not 1 <= rate_window <= 86400):
                raise ValueError("rate_limit contains invalid bounds or identity")
            rate_limit_spec = (rate_actor, rate_action, rate_count, rate_window)
        receipt = None
        if submission_receipt is not None:
            if not isinstance(submission_receipt, dict) or set(submission_receipt) != {"actor_id", "approval_id", "task_fingerprint"}:
                raise ValueError("submission_receipt has an invalid shape")
            actor_id, approval_id, task_fingerprint = (submission_receipt["actor_id"], submission_receipt["approval_id"], submission_receipt["task_fingerprint"])
            if (not isinstance(actor_id, str) or not actor_id.strip() or len(actor_id) > 200
                    or not isinstance(approval_id, str) or not approval_id.strip() or len(approval_id) > 128
                    or not isinstance(task_fingerprint, str) or len(task_fingerprint) != 64
                    or any(ch not in "0123456789abcdef" for ch in task_fingerprint)):
                raise ValueError("submission_receipt contains invalid identity or fingerprint fields")
            receipt = (actor_id, approval_id, task_fingerprint)
        task_id = str(uuid.uuid4())
        with closing(self._connect()) as c:
            c.execute("BEGIN IMMEDIATE")
            if receipt is not None:
                prior = c.execute("SELECT * FROM submission_receipts WHERE goal_id=? AND idempotency_key=?", (goal_id, idempotency_key)).fetchone()
                if prior is not None:
                    if (prior["actor_id"], prior["approval_id"], prior["task_fingerprint"]) != receipt:
                        raise TaskConflict("idempotency key is already bound to a different actor, approval, or task")
                    existing_task = c.execute("SELECT * FROM tasks WHERE id=?", (prior["task_id"],)).fetchone()
                    if existing_task is None:
                        raise TaskConflict("submission receipt references a missing task")
                    c.commit()
                    return self._decode(existing_task)
            goal = c.execute("SELECT state FROM goals WHERE id=?",(goal_id,)).fetchone()
            if goal is None: raise TaskNotFound("goal not found")
            old = c.execute("SELECT * FROM tasks WHERE goal_id=? AND idempotency_key=?",(goal_id,idempotency_key)).fetchone()
            if old is not None:
                # Exact replays stay idempotent even after the goal becomes
                # terminal; returning an existing task never reopens the goal.
                if old["submission_fingerprint"] is None or old["submission_fingerprint"] != submission_fingerprint:
                    raise TaskConflict("idempotency key reused with different or unverifiable submission options")
                if receipt is not None:
                    c.execute("INSERT INTO submission_receipts(goal_id,idempotency_key,actor_id,approval_id,task_fingerprint,task_id,created_at) VALUES(?,?,?,?,?,?,?)",
                              (goal_id, idempotency_key, receipt[0], receipt[1], receipt[2], old["id"], now))
                c.commit()
                return self._decode(old)
            if goal["state"]!="active": raise TaskConflict("goal is not active")
            if rate_limit_spec is not None:
                rate_actor, rate_action, rate_count, rate_window = rate_limit_spec
                cutoff = now - rate_window
                c.execute(
                    "DELETE FROM rate_limit_events WHERE actor_id=? AND action=? AND occurred_at<=?",
                    (rate_actor, rate_action, cutoff),
                )
                rate_used = c.execute(
                    "SELECT COUNT(*) AS n FROM rate_limit_events WHERE actor_id=? AND action=? AND occurred_at>?",
                    (rate_actor, rate_action, cutoff),
                ).fetchone()["n"]
                if rate_used >= rate_count:
                    raise RateLimitExceeded("task submission rate limit exceeded")
                c.execute(
                    "INSERT INTO rate_limit_events(actor_id,action,occurred_at) VALUES(?,?,?)",
                    (rate_actor, rate_action, now),
                )
            if owner_id is not None:
                if not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 200:
                    raise ValueError("invalid task owner")
                owner = c.execute("SELECT owner_id FROM goal_owners WHERE goal_id=?", (goal_id,)).fetchone()
                if owner is None or owner["owner_id"] != owner_id:
                    raise TaskConflict("task owner does not match goal owner")
                if isinstance(max_active_tasks, bool) or not isinstance(max_active_tasks, int) or not 1 <= max_active_tasks <= 1000:
                    raise ValueError("max_active_tasks must be 1..1000")
                active = c.execute(
                    "SELECT COUNT(*) AS n FROM tasks t JOIN goal_owners o ON o.goal_id=t.goal_id "
                    "WHERE o.owner_id=? AND t.task_type=? AND t.state IN ('queued','running')",
                    (owner_id, task_type),
                ).fetchone()["n"]
                if active >= max_active_tasks:
                    raise RateLimitExceeded("owner active sandbox task quota exceeded")
            for dep in deps:
                d = c.execute("SELECT goal_id FROM tasks WHERE id=?",(dep,)).fetchone()
                if d is None or d["goal_id"]!=goal_id: raise TaskConflict("dependency must belong to same goal")
            c.execute("INSERT INTO tasks(id,goal_id,task_type,payload_json,state,idempotency_key,submission_fingerprint,max_attempts,available_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      (task_id,goal_id,task_type,payload_json,"queued",idempotency_key,submission_fingerprint,max_attempts,now+delay_seconds,now,now))
            for dep in deps: c.execute("INSERT INTO dependencies VALUES(?,?)",(task_id,dep))
            if receipt is not None:
                c.execute("INSERT INTO submission_receipts(goal_id,idempotency_key,actor_id,approval_id,task_fingerprint,task_id,created_at) VALUES(?,?,?,?,?,?,?)",
                          (goal_id, idempotency_key, receipt[0], receipt[1], receipt[2], task_id, now))
            c.execute("UPDATE goals SET updated_at=? WHERE id=?",(now,goal_id))
            self._event(c,goal_id,task_id,"task.queued",{"task_type":task_type,"dependencies":list(deps)})
            c.commit()
        return self.get_task(task_id)

    def get_submission_receipt(self, goal_id, idempotency_key, *, actor_id, approval_id, task_fingerprint):
        """Return the exact prior task, rejecting any attempt to rebind its key."""
        with closing(self._connect()) as c:
            row = c.execute("SELECT * FROM submission_receipts WHERE goal_id=? AND idempotency_key=?", (goal_id, idempotency_key)).fetchone()
            if row is None:
                return None
            expected = (actor_id, approval_id, task_fingerprint)
            actual = (row["actor_id"], row["approval_id"], row["task_fingerprint"])
            if actual != expected:
                raise TaskConflict("idempotency key is already bound to a different actor, approval, or task")
            task = c.execute("SELECT * FROM tasks WHERE id=?", (row["task_id"],)).fetchone()
            if task is None:
                raise TaskConflict("submission receipt references a missing task")
            return self._decode(task)

    def claim(self, worker_id, *, lease_seconds=60, task_types=None):
        if not isinstance(worker_id,str) or not 1 <= len(worker_id) <= 256: raise ValueError("invalid worker_id")
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or not 1 <= lease_seconds <= 3600: raise ValueError("lease_seconds must be an integer in 1..3600")
        if task_types is not None:
            if (not isinstance(task_types, (tuple, list)) or not 1 <= len(task_types) <= 32
                    or any(not isinstance(value, str) or not 1 <= len(value) <= 128 for value in task_types)
                    or len(set(task_types)) != len(task_types)):
                raise ValueError("task_types must contain 1..32 unique bounded task type names")
        now=self.clock()
        with closing(self._connect()) as c:
            c.execute("BEGIN IMMEDIATE")
            query = """SELECT t.* FROM tasks t JOIN goals g ON g.id=t.goal_id
              WHERE t.state='queued' AND t.available_at<=? AND g.state='active'
              AND NOT EXISTS(SELECT 1 FROM dependencies d JOIN tasks p ON p.id=d.depends_on
                WHERE d.task_id=t.id AND p.state<>'succeeded')"""
            params = [now]
            if task_types is not None:
                query += " AND t.task_type IN (" + ",".join("?" for _ in task_types) + ")"
                params.extend(task_types)
            query += " ORDER BY t.available_at,t.created_at,t.id LIMIT 1"
            row=c.execute(query,params).fetchone()
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

    def fail_unknown_outcome(self, task_id, worker_id):
        """Terminally fail a task whose external side-effect outcome is uncertain.

        This state is deliberately non-retryable: retrying could duplicate an
        operation that completed despite a lost response or audit failure.
        """
        now = self.clock()
        safe_error = "external side-effect outcome is unknown; manual reconciliation required"
        with closing(self._connect()) as c:
            c.execute("BEGIN IMMEDIATE")
            r = self._owned(c, task_id, worker_id)
            c.execute(
                """UPDATE tasks SET state='failed',last_error=?,lease_owner=NULL,
                   lease_until=NULL,updated_at=? WHERE id=?""",
                (safe_error, now, task_id),
            )
            self._event(
                c, r["goal_id"], task_id, "task.outcome_unknown",
                {"attempt": r["attempts"], "error": safe_error},
            )
            self._fail_blocked_descendants(c, r["goal_id"], now)
            self._complete_goal_if_terminal(c, r["goal_id"], now)
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

    def list_unknown_outcomes(self, *, limit=100):
        """Return metadata-only records requiring operator reconciliation."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        with closing(self._connect()) as c:
            rows = c.execute(
                """SELECT t.id,t.goal_id,t.state,t.attempts,t.last_error,
                          t.created_at,t.updated_at,MAX(e.created_at) AS unknown_at
                   FROM tasks t
                   JOIN run_events e ON e.task_id=t.id
                   WHERE t.task_type='sandbox.execution.v1'
                     AND t.state='failed' AND e.event_type='task.outcome_unknown'
                   GROUP BY t.id
                   ORDER BY unknown_at,t.id LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_task(self,task_id):
        with closing(self._connect()) as c: r=c.execute("SELECT * FROM tasks WHERE id=?",(task_id,)).fetchone()
        if r is None: raise TaskNotFound("task not found")
        return self._decode(r)

    def get_goal(self,goal_id):
        with closing(self._connect()) as c: r=c.execute("SELECT * FROM goals WHERE id=?",(goal_id,)).fetchone()
        if r is None: raise TaskNotFound("goal not found")
        return dict(r)

    def list_tasks(self,goal_id):
        with closing(self._connect()) as c: rows=c.execute("SELECT * FROM tasks WHERE goal_id=? ORDER BY created_at,id",(goal_id,)).fetchall()
        return [self._decode(r) for r in rows]

    def list_events(self,goal_id,*,after_id=0,limit=100):
        if not 1<=limit<=500: raise ValueError("limit must be 1..500")
        with closing(self._connect()) as c: rows=c.execute("SELECT * FROM run_events WHERE goal_id=? AND id>? ORDER BY id LIMIT ?",(goal_id,after_id,limit)).fetchall()
        return [{"id":r["id"],"goal_id":r["goal_id"],"task_id":r["task_id"],"event_type":r["event_type"],"detail":json.loads(r["detail_json"]),"created_at":r["created_at"]} for r in rows]
