"""PostgreSQL-backed shared workflow repository with transactional lease fencing.

All state/event writes share one PostgreSQL transaction. Claims use SKIP LOCKED,
lease validity uses the database clock, and worker mutations are fenced by owner,
opaque lease generation, expiry, and optimistic task version.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import secrets
from typing import Any, Callable

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from automation.workflow_store import (
    WorkflowConflict, WorkflowRun, WorkflowTransitionError,
    _MAX_KEY_CHARS, _MAX_NAME_CHARS, _MAX_DEFINITION_BYTES, _MAX_RESULT_BYTES,
    _STATES, _TERMINAL, _TRANSITIONS, _bounded_json,
    WorkflowRunStore,
)


class PostgresWorkflowRunStore:
    """WorkflowRepository implementation for multi-process/multi-host workers."""

    supports_tenant_ownership = True

    def __init__(
        self, database_url: str, *, min_pool_size: int = 1,
        max_pool_size: int = 10, pool_timeout: float = 10.0,
    ):
        if not isinstance(database_url, str) or not database_url.strip() or len(database_url) > 4096:
            raise ValueError("workflow_database_url_invalid")
        if type(min_pool_size) is not int or type(max_pool_size) is not int or not 1 <= min_pool_size <= max_pool_size <= 50:
            raise ValueError("workflow_pool_size_invalid")
        if isinstance(pool_timeout, bool) or not isinstance(pool_timeout, (int, float)) or not 1 <= pool_timeout <= 120:
            raise ValueError("workflow_pool_timeout_invalid")
        self.database_url = database_url
        self.database_identity = "postgres:" + hashlib.sha256(database_url.strip().encode("utf-8")).hexdigest()
        self.pool = ConnectionPool(
            conninfo=database_url, min_size=min_pool_size, max_size=max_pool_size,
            timeout=float(pool_timeout), kwargs={"row_factory": dict_row}, open=True,
        )
        try:
            self.pool.wait(timeout=float(pool_timeout))
            self.check_ready()
        except Exception:
            self.pool.close()
            raise

    @staticmethod
    def _iso(value):
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc).isoformat()
        return str(value)

    @classmethod
    def _row(cls, row):
        if row is None:
            return None
        definition = row["definition_json"]
        result = row["result_json"]
        if isinstance(definition, str):
            definition = json.loads(definition)
        if isinstance(result, str):
            result = json.loads(result)
        return WorkflowRun(
            run_id=row["run_id"], idempotency_key=row["idempotency_key"],
            workflow_name=row["workflow_name"], definition=definition,
            state=row["state"], result=result, error_code=row["error_code"],
            created_at=cls._iso(row["created_at"]), updated_at=cls._iso(row["updated_at"]),
            version=int(row["version"]), attempts=int(row["attempts"]),
            lease_owner=row["lease_owner"], lease_expires_at=cls._iso(row["lease_expires_at"]),
            lease_generation=(str(row["lease_generation"]) if row["state"] == "running" else None), approved_by=row["approved_by"],
            approved_at=cls._iso(row["approved_at"]),
            approved_session_hash=row["approved_session_hash"], approval_id=row["approval_id"],
            tenant_id=row.get("tenant_id"), owner_principal_id=row.get("owner_principal_id"),
            ownership_status=row.get("ownership_status", "legacy_unassigned"),
        )

    @staticmethod
    def _generation(value):
        if value is None:
            return None
        if not isinstance(value, str) or not value.isdecimal():
            raise ValueError("expected_lease_generation must be a decimal generation.")
        return int(value)

    @staticmethod
    def _validate_version(value):
        if type(value) is not int or value < 0:
            raise ValueError("expected_version must be a non-negative integer.")

    @staticmethod
    def _validate_lease_fence(owner, generation):
        if owner is None and generation is None:
            return
        if (not isinstance(owner, str) or not owner.strip()
                or len(owner) > 200 or "\x00" in owner or generation is None):
            raise ValueError("expected_lease_owner_and_generation_required")

    @staticmethod
    def _requires_approval(definition):
        return WorkflowRunStore._requires_approval(definition)

    @staticmethod
    def _append_event(conn, task_id, *, kind, actor_id, payload, expected_version):
        from automation.task_runtime import TaskEvent, _EVENT_KINDS
        if kind not in _EVENT_KINDS:
            raise ValueError("Unknown task event kind.")
        if not isinstance(actor_id, str) or not actor_id.strip() or len(actor_id) > 200 or "\x00" in actor_id:
            raise ValueError("actor_id must be non-empty bounded text.")
        if not isinstance(payload, dict):
            raise ValueError("task event payload must be an object.")
        encoded = _bounded_json(payload, 8 * 1024, "task event payload")
        row = conn.execute("SELECT state, version FROM workflow_runs WHERE run_id=%s", (task_id,)).fetchone()
        if row is None:
            raise KeyError("task not found.")
        if int(row["version"]) != expected_version:
            raise WorkflowConflict("task version changed; reload before appending an event.")
        if row["state"] in _TERMINAL:
            expected_terminal = {
                "succeeded": "task.completed", "failed": "task.failed",
                "cancelled": "task.cancelled", "interrupted": "task.interrupted",
            }.get(row["state"])
            if kind != expected_terminal:
                raise WorkflowTransitionError("cannot append events to a terminal task.")
            prior = conn.execute(
                "SELECT 1 FROM task_events WHERE task_id=%s AND kind IN "
                "('task.completed','task.failed','task.cancelled','task.interrupted') LIMIT 1",
                (task_id,),
            ).fetchone()
            if prior:
                raise WorkflowTransitionError("terminal event has already been recorded.")
        updated = conn.execute(
            "UPDATE workflow_runs SET updated_at=clock_timestamp(), version=version+1 "
            "WHERE run_id=%s AND version=%s RETURNING version", (task_id, expected_version),
        )
        if updated.fetchone() is None:
            raise WorkflowConflict("task changed concurrently.")
        sequence = conn.execute(
            "SELECT COALESCE(MAX(sequence),0)+1 AS next_sequence FROM task_events WHERE task_id=%s",
            (task_id,),
        ).fetchone()["next_sequence"]
        event = conn.execute(
            "INSERT INTO task_events(task_id,sequence,kind,actor_id,payload_json,created_at) "
            "VALUES (%s,%s,%s,%s,%s,clock_timestamp()) "
            "RETURNING event_id,created_at",
            (task_id, sequence, kind, actor_id.strip(), Jsonb(json.loads(encoded))),
        ).fetchone()
        return TaskEvent(
            int(event["event_id"]), task_id, int(sequence), kind, actor_id.strip(),
            json.loads(encoded), PostgresWorkflowRunStore._iso(event["created_at"]),
        )

    def create(self, *, idempotency_key: str, workflow_name: str, definition: list[dict[str, Any]], tenant_id: str | None = None, owner_principal_id: str | None = None) -> WorkflowRun:
        if (not isinstance(idempotency_key, str) or not idempotency_key.strip()
                or len(idempotency_key) > _MAX_KEY_CHARS or "\x00" in idempotency_key):
            raise ValueError("idempotency_key must be non-empty text of at most 200 characters.")
        key = idempotency_key.strip()
        name = WorkflowRunStore._validate_name(workflow_name)
        canonical = WorkflowRunStore._validate_definition(definition)
        definition_json = _bounded_json(canonical, _MAX_DEFINITION_BYTES, "definition")
        if (tenant_id is None) != (owner_principal_id is None):
            raise ValueError("tenant_id_and_owner_principal_id_must_be_supplied_together")
        if tenant_id is not None:
            for value, field in ((tenant_id, "tenant_id"), (owner_principal_id, "owner_principal_id")):
                if not isinstance(value, str) or not value.strip() or len(value) > 200 or chr(0) in value:
                    raise ValueError(field + "_invalid")
            tenant_id, owner_principal_id = tenant_id.strip(), owner_principal_id.strip()
        if tenant_id is None:
            # Preserve the pre-ownership digest for legacy idempotent retries.
            digest = hashlib.sha256((name + "\n" + definition_json).encode("utf-8")).hexdigest()
        else:
            digest_payload = {"workflow_name": name, "definition": canonical,
                              "tenant_id": tenant_id, "owner_principal_id": owner_principal_id}
            digest = hashlib.sha256(json.dumps(
                digest_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")).hexdigest()
        run_id = secrets.token_urlsafe(18)
        with self.pool.connection() as conn:
            with conn.transaction():
                inserted = conn.execute(
                    "INSERT INTO workflow_runs(run_id,idempotency_key,request_digest,workflow_name,definition_json,state,tenant_id,owner_principal_id,ownership_status) "
                    "VALUES (%s,%s,%s,%s,%s,'queued',%s,%s,%s) ON CONFLICT(idempotency_key) DO NOTHING RETURNING run_id",
                    (run_id, key, digest, name, Jsonb(canonical), tenant_id, owner_principal_id,
                     "owned" if tenant_id is not None else "legacy_unassigned"),
                ).fetchone()
                row = conn.execute("SELECT * FROM workflow_runs WHERE idempotency_key=%s", (key,)).fetchone()
                if row is None:
                    raise RuntimeError("workflow_create_failed")
                if row["request_digest"] != digest:
                    raise WorkflowConflict("idempotency key was already used for a different workflow.")
                return self._row(row)

    def get_by_idempotency_key(self, idempotency_key: str) -> WorkflowRun | None:
        """Find a workflow by its stable enqueue key for authorized recovery."""
        if (
            not isinstance(idempotency_key, str)
            or not idempotency_key.strip()
            or len(idempotency_key) > _MAX_KEY_CHARS
            or "\\x00" in idempotency_key
        ):
            raise ValueError("idempotency_key must be non-empty bounded text.")
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM workflow_runs WHERE idempotency_key=%s",
                (idempotency_key.strip(),),
            ).fetchone()
        return self._row(row)

    def get(self, run_id: str) -> WorkflowRun | None:
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("run_id must be non-empty text.")
        with self.pool.connection() as conn:
            return self._row(conn.execute("SELECT * FROM workflow_runs WHERE run_id=%s", (run_id,)).fetchone())

    def list_runs(self, *, state: str | None = None, limit: int = 50) -> list[WorkflowRun]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer from 1 to 100.")
        if state is not None and state not in _STATES:
            raise ValueError("Unknown workflow state.")
        with self.pool.connection() as conn:
            if state is None:
                rows = conn.execute("SELECT * FROM workflow_runs ORDER BY created_at DESC,run_id LIMIT %s", (limit,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM workflow_runs WHERE state=%s ORDER BY created_at DESC,run_id LIMIT %s", (state,limit)).fetchall()
        return [self._row(row) for row in rows]

    def list_queued_approved_by_session(self, session_hash: str, *, limit: int = 100, after: tuple[str, str] | None = None) -> list[WorkflowRun]:
        if not isinstance(session_hash, str) or len(session_hash) != 64 or any(c not in "0123456789abcdef" for c in session_hash):
            raise ValueError("session_hash must be a lowercase SHA-256 digest.")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer from 1 to 100.")
        if after is not None and (not isinstance(after, tuple) or len(after) != 2 or not all(isinstance(v,str) and v for v in after)):
            raise ValueError("after must be a (created_at, run_id) cursor.")
        with self.pool.connection() as conn:
            if after is None:
                rows=conn.execute("SELECT * FROM workflow_runs WHERE state='queued' AND approved_session_hash=%s AND approved_by IS NOT NULL ORDER BY created_at,run_id LIMIT %s",(session_hash,limit)).fetchall()
            else:
                try:
                    cursor_time=datetime.fromisoformat(after[0])
                except ValueError as exc:
                    raise ValueError("after cursor timestamp is invalid.") from exc
                rows=conn.execute("SELECT * FROM workflow_runs WHERE state='queued' AND approved_session_hash=%s AND approved_by IS NOT NULL AND (created_at>%s OR (created_at=%s AND run_id>%s)) ORDER BY created_at,run_id LIMIT %s",(session_hash,cursor_time,cursor_time,after[1],limit)).fetchall()
        return [self._row(row) for row in rows]

    def claim_next(self, *, worker_id: str, lease_seconds: int = 60) -> WorkflowRun | None:
        if not isinstance(worker_id,str) or not worker_id.strip() or len(worker_id)>200 or "\x00" in worker_id:
            raise ValueError("worker_id must be non-empty bounded text.")
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be an integer from 1 to 3600.")
        sql="""WITH candidate AS (
            SELECT run_id FROM workflow_runs WHERE state='queued'
              AND (NOT EXISTS (SELECT 1 FROM jsonb_array_elements(definition_json) AS item(step)
                               WHERE COALESCE((step->>'requires_approval')::boolean, TRUE))
                   OR (approved_by IS NOT NULL AND approved_at IS NOT NULL
                       AND approved_session_hash IS NOT NULL AND approval_digest=request_digest))
            ORDER BY created_at,run_id LIMIT 1 FOR UPDATE SKIP LOCKED
        )
        UPDATE workflow_runs AS w SET state='running',updated_at=clock_timestamp(),
            version=w.version+1,attempts=w.attempts+1,lease_owner=%s,
            lease_expires_at=clock_timestamp()+(%s * interval '1 second'),lease_generation=w.lease_generation+1
        FROM candidate WHERE w.run_id=candidate.run_id RETURNING w.*"""
        with self.pool.connection() as conn:
            with conn.transaction():
                row=conn.execute(sql,(worker_id.strip(),lease_seconds)).fetchone()
        return self._row(row)

    def renew_lease(self, run_id: str, *, worker_id: str, expected_version: int, lease_seconds: int = 60, delegation_fence: Callable[[Any], None] | None = None) -> WorkflowRun:
        if not isinstance(worker_id,str) or not worker_id.strip() or len(worker_id)>200:
            raise ValueError("worker_id must be non-empty bounded text.")
        self._validate_version(expected_version)
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be an integer from 1 to 3600.")
        with self.pool.connection() as conn:
            with conn.transaction():
                if delegation_fence is not None:
                    delegation_fence(conn)
                row=conn.execute(
                    "UPDATE workflow_runs SET lease_expires_at=clock_timestamp()+(%s * interval '1 second'),"
                    "updated_at=clock_timestamp(),version=version+1 "
                    "WHERE run_id=%s AND version=%s AND state='running' AND lease_owner=%s "
                    "AND lease_expires_at>clock_timestamp() RETURNING *",
                    (lease_seconds,run_id,expected_version,worker_id.strip()),
                ).fetchone()
                if row is None:
                    current=conn.execute("SELECT state,lease_owner FROM workflow_runs WHERE run_id=%s",(run_id,)).fetchone()
                    if current is None: raise KeyError("workflow run not found.")
                    if current["state"]!="running" or current["lease_owner"]!=worker_id.strip():
                        raise WorkflowTransitionError("only the current worker may renew a running lease.")
                    raise WorkflowConflict("workflow run changed or lease expired.")
                return self._row(row)

    def approve_with_event(self, run_id: str, *, actor_id: str, session_id: str, expected_version: int) -> WorkflowRun:
        if not isinstance(actor_id,str) or not actor_id.strip() or len(actor_id)>200 or "\x00" in actor_id:
            raise ValueError("actor_id must be non-empty bounded text.")
        if not isinstance(session_id,str) or not session_id.strip() or len(session_id)>512 or "\x00" in session_id:
            raise ValueError("session_id must be non-empty bounded text.")
        self._validate_version(expected_version)
        session_hash=hashlib.sha256(session_id.encode("utf-8")).hexdigest()
        approval_id=secrets.token_urlsafe(24)
        with self.pool.connection() as conn:
            with conn.transaction():
                row=conn.execute(
                    "UPDATE workflow_runs SET approved_by=%s,approved_at=clock_timestamp(),approval_id=%s,"
                    "approval_digest=request_digest,approved_session_hash=%s,updated_at=clock_timestamp(),version=version+1 "
                    "WHERE run_id=%s AND version=%s AND state='queued' RETURNING *",
                    (actor_id.strip(),approval_id,session_hash,run_id,expected_version),
                ).fetchone()
                if row is None:
                    current=conn.execute("SELECT state,version FROM workflow_runs WHERE run_id=%s",(run_id,)).fetchone()
                    if current is None: raise KeyError("workflow run not found.")
                    if int(current["version"])!=expected_version: raise WorkflowConflict("workflow run version changed; reload before approving.")
                    raise WorkflowTransitionError("only queued workflows can be approved.")
                self._append_event(conn,run_id,kind="task.approved",actor_id=actor_id,payload={"state_version":expected_version+1},expected_version=expected_version+1)
                return self._row(conn.execute("SELECT * FROM workflow_runs WHERE run_id=%s",(run_id,)).fetchone())

    def transition_with_event(self, run_id: str, *, expected_version: int, to_state: str, event_kind: str, actor_id: str, event_payload: dict[str,Any], result: dict[str,Any] | list[Any] | None = None, error_code: str | None = None, expected_lease_owner: str | None = None, expected_lease_generation: str | None = None, delegation_fence: Callable[[Any],None] | None = None) -> WorkflowRun:
        if to_state not in _STATES: raise ValueError("Unknown workflow state.")
        terminal={"succeeded":"task.completed","failed":"task.failed","cancelled":"task.cancelled","interrupted":"task.interrupted"}
        expected_event=terminal.get(to_state)
        if (expected_event is not None and event_kind!=expected_event) or (expected_event is None and event_kind in terminal.values()):
            raise ValueError("event_kind must match the resulting terminal state.")
        self._validate_version(expected_version)
        expected_generation = self._generation(expected_lease_generation)
        self._validate_lease_fence(expected_lease_owner, expected_generation)
        if error_code is not None and (not isinstance(error_code,str) or not error_code or len(error_code)>100 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for c in error_code)):
            raise ValueError("error_code must be a bounded machine-readable code.")
        result_json=None if result is None else _bounded_json(result,_MAX_RESULT_BYTES,"result")
        with self.pool.connection() as conn:
            with conn.transaction():
                if delegation_fence is not None: delegation_fence(conn)
                current=conn.execute("SELECT * FROM workflow_runs WHERE run_id=%s FOR UPDATE",(run_id,)).fetchone()
                if current is None: raise KeyError("workflow run not found.")
                if int(current["version"])!=expected_version: raise WorkflowConflict("workflow run version changed; reload before updating.")
                if to_state=="running" and self._requires_approval(current["definition_json"] if isinstance(current["definition_json"],list) else json.loads(current["definition_json"])):
                    if not (current["approved_by"] and current["approved_at"] and current["approved_session_hash"] and current["approval_digest"]==current["request_digest"]):
                        raise WorkflowTransitionError("workflow requires explicit approval before execution.")
                if to_state not in _TRANSITIONS[current["state"]]:
                    raise WorkflowTransitionError(f"transition {current['state']!r} -> {to_state!r} is not allowed.")
                if expected_lease_owner is not None and (current["state"]!="running" or current["lease_owner"]!=expected_lease_owner or current["lease_expires_at"] is None):
                    raise WorkflowConflict("active worker lease changed or expired.")
                if expected_lease_generation is not None and current["lease_generation"]!=expected_generation:
                    raise WorkflowConflict("worker lease generation changed.")
                updated=conn.execute(
                    "UPDATE workflow_runs SET state=%s,result_json=%s,error_code=%s,updated_at=clock_timestamp(),"
                    "version=version+1,lease_owner=NULL,lease_expires_at=NULL "
                    "WHERE run_id=%s AND version=%s "
                    "AND (%s IS NULL OR (state='running' AND lease_owner=%s AND lease_expires_at>clock_timestamp())) "
                    "AND (%s IS NULL OR lease_generation=%s::bigint) RETURNING run_id",
                    (to_state,Jsonb(json.loads(result_json)) if result_json else None,error_code,run_id,expected_version,
                     expected_lease_owner,expected_lease_owner,expected_generation,expected_generation),
                ).fetchone()
                if updated is None: raise WorkflowConflict("workflow run changed or lease expired.")
                self._append_event(conn,run_id,kind=event_kind,actor_id=actor_id,payload=event_payload,expected_version=expected_version+1)
                return self._row(conn.execute("SELECT * FROM workflow_runs WHERE run_id=%s",(run_id,)).fetchone())

    def append_task_event(self, task_id: str, *, kind: str, actor_id: str, payload: dict[str,Any], expected_version: int, expected_lease_owner: str | None = None, expected_lease_generation: str | None = None, delegation_fence: Callable[[Any],None] | None = None):
        from automation.task_runtime import _EVENT_KINDS
        if kind not in _EVENT_KINDS: raise ValueError("Unknown task event kind.")
        self._validate_version(expected_version)
        expected_generation = self._generation(expected_lease_generation)
        self._validate_lease_fence(expected_lease_owner, expected_generation)
        with self.pool.connection() as conn:
            with conn.transaction():
                if delegation_fence is not None: delegation_fence(conn)
                row=conn.execute("SELECT * FROM workflow_runs WHERE run_id=%s FOR UPDATE",(task_id,)).fetchone()
                if row is None: raise KeyError("task not found.")
                if int(row["version"])!=expected_version: raise WorkflowConflict("task version changed; reload before appending an event.")
                if expected_lease_owner is not None and (row["state"]!="running" or row["lease_owner"]!=expected_lease_owner or row["lease_expires_at"] is None):
                    raise WorkflowConflict("active worker lease changed or expired.")
                if expected_lease_generation is not None and row["lease_generation"]!=expected_generation:
                    raise WorkflowConflict("worker lease generation changed.")
                if expected_lease_owner is not None:
                    valid=conn.execute("SELECT 1 FROM workflow_runs WHERE run_id=%s AND lease_expires_at>clock_timestamp()",(task_id,)).fetchone()
                    if valid is None: raise WorkflowConflict("active worker lease changed or expired.")
                return self._append_event(conn,task_id,kind=kind,actor_id=actor_id,payload=payload,expected_version=expected_version)

    def list_task_events(self, task_id: str, *, after_sequence: int = 0, limit: int = 100):
        from automation.task_runtime import TaskEvent
        if not isinstance(task_id,str) or not task_id: raise ValueError("task_id must be non-empty text.")
        if type(after_sequence) is not int or after_sequence<0: raise ValueError("after_sequence must be a non-negative integer.")
        if type(limit) is not int or not 1<=limit<=500: raise ValueError("limit must be an integer from 1 to 500.")
        with self.pool.connection() as conn:
            rows=conn.execute("SELECT * FROM task_events WHERE task_id=%s AND sequence>%s ORDER BY sequence LIMIT %s",(task_id,after_sequence,limit)).fetchall()
        return [TaskEvent(int(r["event_id"]),r["task_id"],int(r["sequence"]),r["kind"],r["actor_id"],r["payload_json"],self._iso(r["created_at"])) for r in rows]

    def recover_expired_with_events(self, *, actor_id: str, limit: int = 50) -> list[WorkflowRun]:
        if not isinstance(actor_id,str) or not actor_id.strip() or len(actor_id)>200 or "\x00" in actor_id: raise ValueError("actor_id must be non-empty bounded text.")
        if type(limit) is not int or not 1<=limit<=100: raise ValueError("limit must be an integer from 1 to 100.")
        changed=[]
        with self.pool.connection() as conn:
            with conn.transaction():
                rows=conn.execute(
                    "SELECT * FROM workflow_runs WHERE state='running' AND lease_expires_at<=clock_timestamp() "
                    "ORDER BY lease_expires_at LIMIT %s FOR UPDATE SKIP LOCKED",(limit,),
                ).fetchall()
                for row in rows:
                    updated=conn.execute(
                        "UPDATE workflow_runs SET state='interrupted',error_code='lease_expired',updated_at=clock_timestamp(),"
                        "version=version+1,lease_owner=NULL,lease_expires_at=NULL "
                        "WHERE run_id=%s AND version=%s RETURNING version",(row["run_id"],row["version"]),
                    ).fetchone()
                    if updated is None: continue
                    self._append_event(conn,row["run_id"],kind="task.interrupted",actor_id=actor_id,
                        payload={"state":"interrupted","error_code":"lease_expired","automatic_replay":False,"state_version":int(row["version"])+1},
                        expected_version=int(row["version"])+1)
                    changed.append(self._row(conn.execute("SELECT * FROM workflow_runs WHERE run_id=%s",(row["run_id"],)).fetchone()))
        return changed

    def check_ready(self):
        with self.pool.connection() as conn:
            conn.execute("SELECT run_id,lease_generation FROM workflow_runs LIMIT 0")
            conn.execute("SELECT event_id,task_id,sequence FROM task_events LIMIT 0")
        return True

    def close(self):
        self.pool.close()
