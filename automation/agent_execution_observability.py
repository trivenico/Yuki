"""Privacy-safe, durable execution tracing for Yuki agents and delegated work.

This store records identifiers, lifecycle events, authorization decisions and
bounded resource counters only. It deliberately has no prompt, message,
credential, tool-payload, or arbitrary-metadata fields.
"""
from __future__ import annotations

from pathlib import Path
import re
import sqlite3
import time
import uuid
from typing import Any, Callable

_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_EVENT = frozenset({
    "execution.created", "execution.started", "execution.completed",
    "execution.failed", "execution.cancelled", "execution.timed_out",
    "agent.delegated", "agent.accepted", "agent.rejected",
    "provider.started", "provider.completed", "provider.failed",
    "tool.started", "tool.completed", "tool.failed",
    "permission.granted", "permission.denied", "execution.recovered",
})
_STATUS = frozenset({"created", "running", "succeeded", "failed", "cancelled", "timed_out", "uncertain"})


class ExecutionTraceError(ValueError):
    """Invalid or conflicting execution trace operation."""


class AgentExecutionTraceStore:
    """SQLite execution tree and append-only lifecycle/resource event journal."""

    def __init__(self, database_path: str, *, clock: Callable[[], float] = time.time):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path_required")
        if not callable(clock):
            raise TypeError("clock_must_be_callable")
        self.database_path = database_path
        self._clock = clock
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def _initialize(self):
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS agent_execution_traces (
                    execution_id TEXT PRIMARY KEY,
                    parent_execution_id TEXT REFERENCES agent_execution_traces(execution_id),
                    root_execution_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    task_id TEXT,
                    provider_execution_id TEXT,
                    status TEXT NOT NULL CHECK(status IN
                        ('created','running','succeeded','failed','cancelled','timed_out','uncertain')),
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    CHECK(parent_execution_id IS NULL OR parent_execution_id != execution_id)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_trace_parent
                    ON agent_execution_traces(parent_execution_id, created_at, execution_id);
                CREATE INDEX IF NOT EXISTS idx_agent_trace_root
                    ON agent_execution_traces(root_execution_id, created_at, execution_id);
                CREATE INDEX IF NOT EXISTS idx_agent_trace_status
                    ON agent_execution_traces(status, updated_at);
                CREATE TABLE IF NOT EXISTS agent_execution_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    execution_id TEXT NOT NULL REFERENCES agent_execution_traces(execution_id),
                    event_type TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    permission TEXT,
                    decision TEXT CHECK(decision IS NULL OR decision IN ('granted','denied','not_applicable')),
                    input_tokens INTEGER NOT NULL DEFAULT 0 CHECK(input_tokens >= 0),
                    output_tokens INTEGER NOT NULL DEFAULT 0 CHECK(output_tokens >= 0),
                    duration_ms INTEGER NOT NULL DEFAULT 0 CHECK(duration_ms >= 0),
                    cost_microunits INTEGER NOT NULL DEFAULT 0 CHECK(cost_microunits >= 0),
                    occurred_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_execution_events
                    ON agent_execution_events(execution_id, event_id);
                CREATE TABLE IF NOT EXISTS agent_execution_outbox (
                    event_id INTEGER PRIMARY KEY REFERENCES agent_execution_events(event_id),
                    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
                    available_at REAL NOT NULL,
                    lease_owner TEXT,
                    lease_until REAL,
                    delivered_at REAL,
                    last_error_code TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_agent_execution_outbox_ready
                    ON agent_execution_outbox(delivered_at, available_at, lease_until, event_id);
                CREATE TRIGGER IF NOT EXISTS agent_execution_events_no_update
                    BEFORE UPDATE ON agent_execution_events
                    BEGIN SELECT RAISE(ABORT, 'execution events are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS agent_execution_events_no_delete
                    BEFORE DELETE ON agent_execution_events
                    BEGIN SELECT RAISE(ABORT, 'execution events are append-only'); END;
            """)

    @staticmethod
    def _identifier(value: str, field: str) -> str:
        if not isinstance(value, str) or not _ID.fullmatch(value):
            raise ExecutionTraceError(field + "_invalid")
        return value

    def start_execution(
        self, *, agent_id: str, parent_execution_id: str | None = None,
        task_id: str | None = None, provider_execution_id: str | None = None,
        execution_id: str | None = None,
    ) -> dict[str, Any]:
        agent = self._identifier(agent_id, "agent_id")
        if parent_execution_id is not None:
            parent_execution_id = self._identifier(parent_execution_id, "parent_execution_id")
        if task_id is not None:
            task_id = self._identifier(task_id, "task_id")
        if provider_execution_id is not None:
            provider_execution_id = self._identifier(provider_execution_id, "provider_execution_id")
        new_id = self._identifier(execution_id or uuid.uuid4().hex, "execution_id")
        now = float(self._clock())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            parent = None
            if parent_execution_id:
                parent = db.execute(
                    "SELECT root_execution_id FROM agent_execution_traces WHERE execution_id=?",
                    (parent_execution_id,),
                ).fetchone()
                if parent is None:
                    db.rollback()
                    raise KeyError("parent_execution_not_found")
            root_id = parent["root_execution_id"] if parent else new_id
            try:
                db.execute(
                    "INSERT INTO agent_execution_traces "
                    "(execution_id,parent_execution_id,root_execution_id,agent_id,task_id,provider_execution_id,status,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,'created',?,?)",
                    (new_id,parent_execution_id,root_id,agent,task_id,provider_execution_id,now,now),
                )
                event = db.execute(
                    "INSERT INTO agent_execution_events "
                    "(execution_id,event_type,actor_id,occurred_at) VALUES (?,'execution.created',?,?)",
                    (new_id,agent,now),
                )
                db.execute(
                    "INSERT INTO agent_execution_outbox(event_id, available_at) VALUES (?, ?)",
                    (int(event.lastrowid), now),
                )
                db.commit()
            except sqlite3.IntegrityError as exc:
                db.rollback()
                raise ExecutionTraceError("execution_id_or_parent_conflict") from exc
        return self.get_execution(new_id)

    def record_event(
        self, *, execution_id: str, event_type: str, actor_id: str,
        permission: str | None = None, decision: str | None = None,
        input_tokens: int = 0, output_tokens: int = 0,
        duration_ms: int = 0, cost_microunits: int = 0,
        status: str | None = None,
    ) -> int:
        execution_id = self._identifier(execution_id, "execution_id")
        actor_id = self._identifier(actor_id, "actor_id")
        if event_type not in _EVENT:
            raise ExecutionTraceError("event_type_not_allowlisted")
        if permission is not None:
            permission = self._identifier(permission, "permission")
        if decision not in {None, "granted", "denied", "not_applicable"}:
            raise ExecutionTraceError("decision_invalid")
        metrics = (input_tokens, output_tokens, duration_ms, cost_microunits)
        if any(type(v) is not int or v < 0 or v > 2**63 - 1 for v in metrics):
            raise ExecutionTraceError("resource_metric_invalid")
        if status is not None and status not in _STATUS:
            raise ExecutionTraceError("execution_status_invalid")
        if (event_type.startswith("permission.") and (permission is None or decision is None)):
            raise ExecutionTraceError("permission_event_requires_permission_and_decision")
        now = float(self._clock())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status FROM agent_execution_traces WHERE execution_id=?", (execution_id,)
            ).fetchone()
            if row is None:
                db.rollback()
                raise KeyError("execution_not_found")
            cur = db.execute(
                "INSERT INTO agent_execution_events "
                "(execution_id,event_type,actor_id,permission,decision,input_tokens,output_tokens,duration_ms,cost_microunits,occurred_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (execution_id,event_type,actor_id,permission,decision,*metrics,now),
            )
            db.execute(
                "INSERT INTO agent_execution_outbox(event_id, available_at) VALUES (?, ?)",
                (int(cur.lastrowid), now),
            )
            if status is not None:
                db.execute(
                    "UPDATE agent_execution_traces SET status=?, updated_at=? WHERE execution_id=?",
                    (status,now,execution_id),
                )
            else:
                db.execute(
                    "UPDATE agent_execution_traces SET updated_at=? WHERE execution_id=?",
                    (now,execution_id),
                )
            db.commit()
            return int(cur.lastrowid)


    def bind_task(self, execution_id: str, *, task_id: str) -> dict[str, Any]:
        execution_id = self._identifier(execution_id, "execution_id")
        task_id = self._identifier(task_id, "task_id")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT task_id FROM agent_execution_traces WHERE execution_id=?", (execution_id,)).fetchone()
            if row is None:
                db.rollback()
                raise KeyError("execution_not_found")
            if row["task_id"] not in (None, task_id):
                db.rollback()
                raise ExecutionTraceError("task_correlation_conflict")
            conflict = db.execute("SELECT execution_id FROM agent_execution_traces WHERE task_id=? AND execution_id<>?", (task_id, execution_id)).fetchone()
            if conflict is not None:
                db.rollback()
                raise ExecutionTraceError("task_already_correlated")
            db.execute("UPDATE agent_execution_traces SET task_id=?, updated_at=? WHERE execution_id=?", (task_id, float(self._clock()), execution_id))
            db.commit()
        return self.get_execution(execution_id)

    def get_execution_by_task(self, task_id: str, *, agent_id: str) -> dict[str, Any] | None:
        task_id = self._identifier(task_id, "task_id")
        agent_id = self._identifier(agent_id, "agent_id")
        with self._connect() as db:
            rows = db.execute("SELECT execution_id FROM agent_execution_traces WHERE task_id=? AND agent_id=? LIMIT 2", (task_id, agent_id)).fetchall()
        if len(rows) > 1:
            raise ExecutionTraceError("task_correlation_ambiguous")
        return self.get_execution(rows[0]["execution_id"]) if rows else None


    def claim_outbox(self, *, worker_id: str, limit: int = 50, lease_seconds: int = 30) -> list[dict[str, Any]]:
        worker_id = self._identifier(worker_id, "worker_id")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("outbox_limit_invalid")
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
            raise ValueError("outbox_lease_invalid")
        now = float(self._clock())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT event_id FROM agent_execution_outbox "
                "WHERE delivered_at IS NULL AND available_at<=? "
                "AND (lease_until IS NULL OR lease_until<=?) "
                "ORDER BY event_id LIMIT ?", (now, now, limit),
            ).fetchall()
            ids = [int(row["event_id"]) for row in rows]
            for event_id in ids:
                db.execute(
                    "UPDATE agent_execution_outbox SET lease_owner=?, lease_until=?, attempts=attempts+1 "
                    "WHERE event_id=? AND delivered_at IS NULL",
                    (worker_id, now + lease_seconds, event_id),
                )
            claimed = []
            for event_id in ids:
                row = db.execute(
                    "SELECT o.event_id,o.attempts,e.execution_id,e.event_type,e.actor_id,e.permission,e.decision,"
                    "e.input_tokens,e.output_tokens,e.duration_ms,e.cost_microunits,e.occurred_at "
                    "FROM agent_execution_outbox o JOIN agent_execution_events e USING(event_id) "
                    "WHERE o.event_id=? AND o.lease_owner=? AND o.delivered_at IS NULL",
                    (event_id, worker_id),
                ).fetchone()
                if row is not None:
                    claimed.append(dict(row))
            db.commit()
        return claimed

    def acknowledge_outbox(self, *, worker_id: str, event_id: int) -> bool:
        worker_id = self._identifier(worker_id, "worker_id")
        if type(event_id) is not int or event_id < 1:
            raise ValueError("outbox_event_id_invalid")
        with self._connect() as db:
            cur = db.execute(
                "UPDATE agent_execution_outbox SET delivered_at=?, lease_owner=NULL, lease_until=NULL "
                "WHERE event_id=? AND lease_owner=? AND delivered_at IS NULL",
                (float(self._clock()), event_id, worker_id),
            )
            return cur.rowcount == 1

    def retry_outbox(self, *, worker_id: str, event_id: int, error_code: str, delay_seconds: int = 30) -> bool:
        worker_id = self._identifier(worker_id, "worker_id")
        if type(event_id) is not int or event_id < 1:
            raise ValueError("outbox_event_id_invalid")
        if not isinstance(error_code, str) or not re.fullmatch(r"[a-z0-9_.-]{1,64}", error_code):
            raise ValueError("outbox_error_code_invalid")
        if type(delay_seconds) is not int or not 1 <= delay_seconds <= 86400:
            raise ValueError("outbox_delay_invalid")
        with self._connect() as db:
            cur = db.execute(
                "UPDATE agent_execution_outbox SET available_at=?, lease_owner=NULL, lease_until=NULL, last_error_code=? "
                "WHERE event_id=? AND lease_owner=? AND delivered_at IS NULL",
                (float(self._clock()) + delay_seconds, error_code, event_id, worker_id),
            )
            return cur.rowcount == 1

    def outbox_stats(self) -> dict[str, int]:
        with self._connect() as db:
            row = db.execute(
                "SELECT SUM(CASE WHEN delivered_at IS NULL THEN 1 ELSE 0 END) AS pending, "
                "SUM(CASE WHEN delivered_at IS NULL AND lease_owner IS NOT NULL THEN 1 ELSE 0 END) AS leased, "
                "SUM(CASE WHEN delivered_at IS NOT NULL THEN 1 ELSE 0 END) AS delivered "
                "FROM agent_execution_outbox"
            ).fetchone()
            return {key: int(row[key] or 0) for key in ("pending", "leased", "delivered")}

    def bind_provider_execution(self, execution_id: str, *, provider_execution_id: str) -> dict[str, Any]:
        execution_id = self._identifier(execution_id, 'execution_id')
        provider_execution_id = self._identifier(provider_execution_id, 'provider_execution_id')
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT provider_execution_id FROM agent_execution_traces WHERE execution_id=?', (execution_id,)).fetchone()
            if row is None:
                db.rollback()
                raise KeyError('execution_not_found')
            if row['provider_execution_id'] not in (None, provider_execution_id):
                db.rollback()
                raise ExecutionTraceError('provider_correlation_conflict')
            conflict = db.execute('SELECT execution_id FROM agent_execution_traces WHERE provider_execution_id=? AND execution_id<>?', (provider_execution_id, execution_id)).fetchone()
            if conflict is not None:
                db.rollback()
                raise ExecutionTraceError('provider_already_correlated')
            db.execute('UPDATE agent_execution_traces SET provider_execution_id=?, updated_at=? WHERE execution_id=?', (provider_execution_id, float(self._clock()), execution_id))
            db.commit()
        return self.get_execution(execution_id)

    def get_execution(self, execution_id: str) -> dict[str, Any] | None:
        execution_id = self._identifier(execution_id, "execution_id")
        with self._connect() as db:
            row = db.execute(
                "SELECT execution_id,parent_execution_id,root_execution_id,agent_id,task_id,provider_execution_id,status,created_at,updated_at "
                "FROM agent_execution_traces WHERE execution_id=?", (execution_id,),
            ).fetchone()
            return dict(row) if row else None

    def list_children(self, parent_execution_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        parent_execution_id = self._identifier(parent_execution_id, "parent_execution_id")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("limit_out_of_range")
        with self._connect() as db:
            rows = db.execute(
                "SELECT execution_id,parent_execution_id,root_execution_id,agent_id,task_id,provider_execution_id,status,created_at,updated_at "
                "FROM agent_execution_traces WHERE parent_execution_id=? ORDER BY created_at,execution_id LIMIT ?",
                (parent_execution_id,limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_events(self, execution_id: str, *, after_event_id: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        execution_id = self._identifier(execution_id, "execution_id")
        if type(after_event_id) is not int or after_event_id < 0:
            raise ValueError("after_event_id_invalid")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("limit_out_of_range")
        with self._connect() as db:
            rows = db.execute(
                "SELECT event_id,execution_id,event_type,actor_id,permission,decision,input_tokens,output_tokens,duration_ms,cost_microunits,occurred_at "
                "FROM agent_execution_events WHERE execution_id=? AND event_id>? ORDER BY event_id LIMIT ?",
                (execution_id,after_event_id,limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def execution_timeline(self, root_execution_id: str, *, max_executions: int = 500, max_events: int = 2000) -> dict[str, Any]:
        """Return a bounded, read-only tree and event timeline for one root."""
        root_execution_id = self._identifier(root_execution_id, 'root_execution_id')
        if type(max_executions) is not int or not 1 <= max_executions <= 2000:
            raise ValueError('max_executions_out_of_range')
        if type(max_events) is not int or not 1 <= max_events <= 10000:
            raise ValueError('max_events_out_of_range')
        with self._connect() as db:
            root = db.execute(
                'SELECT execution_id,parent_execution_id,root_execution_id,agent_id,task_id,provider_execution_id,status,created_at,updated_at FROM agent_execution_traces WHERE execution_id=? AND root_execution_id=?',
                (root_execution_id, root_execution_id),
            ).fetchone()
            if root is None:
                raise KeyError('root_execution_not_found')
            executions = db.execute(
                'SELECT execution_id,parent_execution_id,root_execution_id,agent_id,task_id,provider_execution_id,status,created_at,updated_at FROM agent_execution_traces WHERE root_execution_id=? ORDER BY created_at,execution_id LIMIT ?',
                (root_execution_id, max_executions + 1),
            ).fetchall()
            if len(executions) > max_executions:
                raise ExecutionTraceError('timeline_execution_limit_exceeded')
            events = db.execute(
                'SELECT e.event_id,e.execution_id,e.event_type,e.actor_id,e.permission,e.decision,e.input_tokens,e.output_tokens,e.duration_ms,e.cost_microunits,e.occurred_at FROM agent_execution_events e JOIN agent_execution_traces t ON t.execution_id=e.execution_id WHERE t.root_execution_id=? ORDER BY e.occurred_at,e.event_id LIMIT ?',
                (root_execution_id, max_events + 1),
            ).fetchall()
            if len(events) > max_events:
                raise ExecutionTraceError('timeline_event_limit_exceeded')
            totals = db.execute(
                'SELECT COALESCE(SUM(e.input_tokens),0) AS input_tokens,COALESCE(SUM(e.output_tokens),0) AS output_tokens,COALESCE(SUM(e.duration_ms),0) AS duration_ms,COALESCE(SUM(e.cost_microunits),0) AS cost_microunits FROM agent_execution_events e JOIN agent_execution_traces t ON t.execution_id=e.execution_id WHERE t.root_execution_id=?',
                (root_execution_id,),
            ).fetchone()
            return {
                'root_execution_id': root_execution_id,
                'executions': [dict(row) for row in executions],
                'events': [dict(row) for row in events],
                'resource_totals': dict(totals),
                'complete': True,
            }

    def resource_totals(self, root_execution_id: str) -> dict[str, int]:
        root_execution_id = self._identifier(root_execution_id, "root_execution_id")
        with self._connect() as db:
            row = db.execute(
                "SELECT COALESCE(SUM(e.input_tokens),0) AS input_tokens,"
                "COALESCE(SUM(e.output_tokens),0) AS output_tokens,"
                "COALESCE(SUM(e.duration_ms),0) AS duration_ms,"
                "COALESCE(SUM(e.cost_microunits),0) AS cost_microunits "
                "FROM agent_execution_events e JOIN agent_execution_traces t USING(execution_id) "
                "WHERE t.root_execution_id=?",
                (root_execution_id,),
            ).fetchone()
            return dict(row)


__all__ = ["AgentExecutionTraceStore", "ExecutionTraceError"]
