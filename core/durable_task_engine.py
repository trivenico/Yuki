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

    def create_goal(self, goal_id=None):
        goal_id, now = goal_id or str(uuid.uuid4()), self.clock()
        with closing(self._connect()) as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute("INSERT INTO goals VALUES(?,?,?,?)", (goal_id,"active",now,now))
            self._event(c,goal_id,None,"goal.created",{})
            c.commit()
        return goal_id

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

    def claim(self, worker_id, *, lease_seconds=60):
        if not isinstance(worker_id,str) or not 1 <= len(worker_id) <= 256: raise ValueError("invalid worker_id")
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or not 1 <= lease_seconds <= 3600: raise ValueError("lease_seconds must be an integer in 1..3600")
        now=self.clock()
        with closing(self._connect()) as c:
            c.execute("BEGIN IMMEDIATE")
            row=c.execute("""SELECT t.* FROM tasks t JOIN goals g ON g.id=t.goal_id
              WHERE t.state='queued' AND t.available_at<=? AND g.state='active'
              AND NOT EXISTS(SELECT 1 FROM dependencies d JOIN tasks p ON p.id=d.depends_on
                WHERE d.task_id=t.id AND p.state<>'succeeded')
              ORDER BY t.available_at,t.created_at,t.id LIMIT 1""",(now,)).fetchone()
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

    def list_tasks(self,goal_id):
        with closing(self._connect()) as c: rows=c.execute("SELECT * FROM tasks WHERE goal_id=? ORDER BY created_at,id",(goal_id,)).fetchall()
        return [self._decode(r) for r in rows]

    def list_events(self,goal_id,*,after_id=0,limit=100):
        if not 1<=limit<=500: raise ValueError("limit must be 1..500")
        with closing(self._connect()) as c: rows=c.execute("SELECT * FROM run_events WHERE goal_id=? AND id>? ORDER BY id LIMIT ?",(goal_id,after_id,limit)).fetchall()
        return [{"id":r["id"],"goal_id":r["goal_id"],"task_id":r["task_id"],"event_type":r["event_type"],"detail":json.loads(r["detail_json"]),"created_at":r["created_at"]} for r in rows]
