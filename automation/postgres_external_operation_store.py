"""PostgreSQL journal for explicit external side-effect intents.

This module deliberately contains no provider client or dispatcher. Expired
in-flight operations become unknown and are never automatically re-queued.
The public lifecycle requires a verified workload context and revalidates
task ownership, task lease, policy revision, and delegation inside PostgreSQL
transactions. Legacy unscoped helpers are private test/migration primitives.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import secrets
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from core.postgres_database_identity import postgres_database_identity

from automation.workflow_store import WorkflowConflict, WorkflowTransitionError


_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_STATES = frozenset({
    "pending", "inflight", "succeeded", "failed_terminal", "unknown",
    "reconciliation_required",
})


@dataclass(frozen=True)
class ExternalOperation:
    operation_id: str
    tenant_id: str
    task_id: str
    agent_id: str | None
    task_lease_generation: str | None
    authorization_decision_id: str | None
    policy_revision: str | None
    step_id: str
    action_name: str
    action_version: str
    idempotency_key: str
    request_digest: str
    input_digest: str
    state: str
    result_digest: str | None
    provider_reference: str | None
    error_code: str | None
    version: int
    attempts: int
    lease_owner: str | None
    lease_expires_at: str | None
    lease_generation: str | None


class PostgresExternalOperationStore:
    """Shared journal with context-bound public APIs and no provider dispatcher."""

    def __init__(self, database_url: str, *, authorize_reconciliation,
                 min_pool_size: int = 1, max_pool_size: int = 10,
                 pool_timeout: float = 10.0):
        if not isinstance(database_url, str) or not database_url.strip() or len(database_url) > 4096:
            raise ValueError("external_operation_database_url_invalid")
        if type(min_pool_size) is not int or type(max_pool_size) is not int or not 1 <= min_pool_size <= max_pool_size <= 50:
            raise ValueError("external_operation_pool_size_invalid")
        if isinstance(pool_timeout, bool) or not isinstance(pool_timeout, (int, float)) or not 1 <= pool_timeout <= 120:
            raise ValueError("external_operation_pool_timeout_invalid")
        if not callable(authorize_reconciliation):
            raise TypeError("external_operation_reconciliation_authorizer_required")
        self.authorize_reconciliation = authorize_reconciliation
        self.database_identity = postgres_database_identity(database_url)
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
    def _validate_text(value: Any, name: str, maximum: int = 128) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\x00" in value:
            raise ValueError(name + "_invalid")
        return value.strip()

    @staticmethod
    def _validate_digest(value: Any, name: str) -> str:
        if not isinstance(value, str) or not _HEX64.fullmatch(value):
            raise ValueError(name + "_invalid")
        return value

    @staticmethod
    def _row(row: dict | None) -> ExternalOperation | None:
        if row is None:
            return None
        expires = row["lease_expires_at"]
        return ExternalOperation(
            operation_id=row["operation_id"], tenant_id=row["tenant_id"],
            task_id=row["task_id"], agent_id=row.get("agent_id"),
            task_lease_generation=(str(row["task_lease_generation"]) if row.get("task_lease_generation") is not None else None),
            authorization_decision_id=row.get("authorization_decision_id"),
            policy_revision=row.get("policy_revision"), step_id=row["step_id"],
            action_name=row["action_name"], action_version=row["action_version"],
            idempotency_key=row["idempotency_key"], request_digest=row["request_digest"],
            input_digest=row["input_digest"], state=row["state"],
            result_digest=row["result_digest"], provider_reference=row["provider_reference"],
            error_code=row["error_code"], version=int(row["version"]),
            attempts=int(row["attempts"]), lease_owner=row["lease_owner"],
            lease_expires_at=expires.isoformat() if expires is not None else None,
            lease_generation=str(row["lease_generation"]) if row["state"] == "inflight" else None,
        )

    @staticmethod
    def _event(conn, operation_id: str, kind: str, actor_id: str,
               payload: dict[str, Any], expected_version: int) -> None:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(encoded.encode("utf-8")) > 4096:
            raise ValueError("external_operation_event_payload_too_large")
        row = conn.execute(
            "SELECT version FROM external_operations WHERE operation_id=%s FOR UPDATE",
            (operation_id,),
        ).fetchone()
        if row is None:
            raise KeyError("external_operation_not_found")
        if int(row["version"]) != expected_version:
            raise WorkflowConflict("external operation version changed")
        seq = conn.execute(
            "SELECT COALESCE(MAX(sequence),0)+1 AS next FROM external_operation_events WHERE operation_id=%s",
            (operation_id,),
        ).fetchone()["next"]
        conn.execute(
            """INSERT INTO external_operation_events
               (operation_id,sequence,kind,actor_id,payload_json,created_at)
               VALUES (%s,%s,%s,%s,%s,clock_timestamp())""",
            (operation_id, seq, kind, actor_id, Jsonb(json.loads(encoded))),
        )

    def _create_unchecked(self, *, tenant_id: str, task_id: str, step_id: str,
               action_name: str, action_version: str, idempotency_key: str,
               input_digest: str) -> ExternalOperation:
        tenant = self._validate_text(tenant_id, "tenant_id")
        task = self._validate_text(task_id, "task_id", 200)
        step = self._validate_text(step_id, "step_id")
        action = self._validate_text(action_name, "action_name")
        version = self._validate_text(action_version, "action_version", 64)
        key = self._validate_text(idempotency_key, "idempotency_key", 200)
        digest = self._validate_digest(input_digest, "input_digest")
        identity = {
            "tenant_id": tenant, "task_id": task, "step_id": step,
            "action_name": action, "action_version": version, "input_digest": digest,
        }
        request_digest = hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        operation_id = secrets.token_hex(16)
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute(
                    """INSERT INTO external_operations
                       (operation_id,tenant_id,task_id,step_id,action_name,action_version,
                        idempotency_key,request_digest,input_digest,state)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending')
                       ON CONFLICT (idempotency_key) DO NOTHING""",
                    (operation_id, tenant, task, step, action, version, key, request_digest, digest),
                )
                row = conn.execute(
                    "SELECT * FROM external_operations WHERE idempotency_key=%s FOR UPDATE",
                    (key,),
                ).fetchone()
                if row is None:
                    raise RuntimeError("external_operation_create_failed")
                if row["request_digest"] != request_digest:
                    raise WorkflowConflict("idempotency key reused for a different operation")
                if row["operation_id"] == operation_id:
                    self._event(conn, operation_id, "operation.created", tenant,
                                {"request_digest": request_digest}, 0)
                return self._row(row)

    @staticmethod
    def _verified_context(context, action_name: str):
        from automation.task_runtime import VerifiedWorkloadContext
        if not isinstance(context, VerifiedWorkloadContext):
            raise PermissionError("verified_workload_context_required")
        tenant = PostgresExternalOperationStore._validate_text(context.tenant_id, "context_tenant_id")
        task = PostgresExternalOperationStore._validate_text(context.task_id, "context_task_id", 200)
        owner = PostgresExternalOperationStore._validate_text(context.owner_principal_id, "context_owner_principal_id", 200)
        subject = PostgresExternalOperationStore._validate_text(context.subject, "context_subject", 200)
        agent = PostgresExternalOperationStore._validate_text(context.agent_id, "context_agent_id", 200)
        action = PostgresExternalOperationStore._validate_text(action_name, "action_name")
        if context.authorized_action != action or action not in context.scopes:
            raise PermissionError("verified_context_action_scope_mismatch")
        if not context.delegation_id or not callable(context.delegation_fence):
            raise PermissionError("verified_delegation_transaction_fence_required")
        if not context.authorization_decision_id or not context.policy_revision:
            raise PermissionError("audited_authorization_decision_required")
        generation = context.lease_generation
        if not isinstance(generation, str) or not generation.isdecimal():
            raise PermissionError("verified_task_lease_generation_invalid")
        if type(context.expires_at) is not int or context.expires_at <= 0:
            raise PermissionError("verified_credential_expiry_invalid")
        return tenant, task, owner, subject, agent, int(generation)

    @staticmethod
    def _lock_authorized_task(conn, context, *, action_name: str):
        tenant, task, owner, subject, agent, generation = PostgresExternalOperationStore._verified_context(
            context, action_name
        )
        row = conn.execute(
            """SELECT tenant_id,owner_principal_id,ownership_status,state,lease_owner,
                      lease_generation,lease_expires_at,
                      floor(extract(epoch FROM clock_timestamp()))::bigint AS db_now,
                      (lease_expires_at > clock_timestamp()) AS lease_active
               FROM workflow_runs WHERE run_id=%s FOR UPDATE""",
            (task,),
        ).fetchone()
        if row is None:
            raise PermissionError("authorized_task_not_found")
        if (
            row["tenant_id"] != tenant
            or row["owner_principal_id"] != owner
            or row["ownership_status"] != "owned"
            or row["state"] != "running"
            or row["lease_owner"] != subject
            or int(row["lease_generation"]) != generation
            or row["lease_active"] is not True
            or context.expires_at <= int(row["db_now"])
        ):
            raise PermissionError("authorized_task_lease_or_ownership_changed")
        context.delegation_fence(conn)
        return tenant, task, owner, subject, agent, generation

    def create_authorized(self, context, *, step_id: str, action_name: str,
                          action_version: str, idempotency_key: str,
                          input_digest: str) -> ExternalOperation:
        """Create an operation only from a verified, audited, delegated context."""
        step = self._validate_text(step_id, "step_id")
        action = self._validate_text(action_name, "action_name")
        version = self._validate_text(action_version, "action_version", 64)
        key = self._validate_text(idempotency_key, "idempotency_key", 200)
        digest = self._validate_digest(input_digest, "input_digest")
        tenant, task, owner, subject, agent, generation = self._verified_context(context, action)
        identity = {
            "tenant_id": tenant, "task_id": task, "agent_id": agent,
            "task_lease_generation": generation,
            "step_id": step, "action_name": action, "action_version": version,
            "input_digest": digest, "policy_revision": context.policy_revision,
        }
        request_digest = hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        operation_id = secrets.token_hex(16)
        with self.pool.connection() as conn:
            with conn.transaction():
                self._lock_authorized_task(conn, context, action_name=action)
                conn.execute(
                    """INSERT INTO external_operations
                       (operation_id,tenant_id,task_id,agent_id,task_lease_generation,
                        authorization_decision_id,policy_revision,step_id,action_name,
                        action_version,idempotency_key,request_digest,input_digest,state)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending')
                       ON CONFLICT (idempotency_key) DO NOTHING""",
                    (operation_id, tenant, task, agent, generation,
                     context.authorization_decision_id, context.policy_revision,
                     step, action, version, key, request_digest, digest),
                )
                row = conn.execute(
                    "SELECT * FROM external_operations WHERE idempotency_key=%s FOR UPDATE",
                    (key,),
                ).fetchone()
                if row is None:
                    raise RuntimeError("external_operation_create_failed")
                if row["request_digest"] != request_digest:
                    raise WorkflowConflict("idempotency key reused for a different authorized operation")
                if row["operation_id"] == operation_id:
                    self._event(
                        conn, operation_id, "operation.created", subject,
                        {"request_digest": request_digest, "agent_id": agent,
                         "task_lease_generation": generation,
                         "authorization_decision_id": context.authorization_decision_id,
                         "policy_revision": context.policy_revision}, 0,
                    )
                return self._row(row)

    def claim_next_authorized(self, context, *, lease_seconds: int = 60) -> ExternalOperation | None:
        """Claim only an operation matching this verified tenant/agent/task/action context."""
        if type(lease_seconds) is not int or not 5 <= lease_seconds <= 3600:
            raise ValueError("external_operation_lease_seconds_invalid")
        action = self._validate_text(context.authorized_action, "authorized_action")
        tenant, task, owner, subject, agent, generation = self._verified_context(context, action)
        with self.pool.connection() as conn:
            with conn.transaction():
                self._lock_authorized_task(conn, context, action_name=action)
                expired = conn.execute(
                    """UPDATE external_operations SET state='unknown',lease_owner=NULL,
                       lease_expires_at=NULL,updated_at=clock_timestamp(),version=version+1
                       WHERE state='inflight' AND tenant_id=%s AND task_id=%s
                         AND action_name=%s AND lease_expires_at <= clock_timestamp()
                       RETURNING operation_id,version""",
                    (tenant, task, action),
                ).fetchall()
                for item in expired:
                    self._event(conn, item["operation_id"], "operation.unknown", "lease-recovery",
                                {"reason": "lease_expired", "automatic_replay": False},
                                int(item["version"]))
                superseded = conn.execute(
                    """UPDATE external_operations SET state='failed_terminal',
                       error_code='authorization_context_superseded',
                       updated_at=clock_timestamp(),version=version+1
                       WHERE state='pending' AND tenant_id=%s AND task_id=%s AND action_name=%s
                         AND (agent_id<>%s OR task_lease_generation<>%s OR policy_revision<>%s)
                       RETURNING operation_id,version""",
                    (tenant, task, action, agent, generation, context.policy_revision),
                ).fetchall()
                for item in superseded:
                    self._event(
                        conn, item["operation_id"], "operation.failed_terminal", "lease-recovery",
                        {"reason": "authorization_context_superseded", "automatic_replay": False},
                        int(item["version"]),
                    )
                row = conn.execute(
                    """WITH candidate AS (
                         SELECT operation_id FROM external_operations
                         WHERE state='pending' AND tenant_id=%s AND agent_id=%s
                           AND task_id=%s AND action_name=%s AND task_lease_generation=%s
                           AND policy_revision=%s
                         ORDER BY created_at,operation_id
                         FOR UPDATE SKIP LOCKED LIMIT 1
                       )
                       UPDATE external_operations AS op SET state='inflight',
                         lease_owner=%s,
                         lease_expires_at=clock_timestamp()+(%s * interval '1 second'),
                         lease_generation=op.lease_generation+1,attempts=op.attempts+1,
                         version=op.version+1,updated_at=clock_timestamp()
                       FROM candidate WHERE op.operation_id=candidate.operation_id
                       RETURNING op.*""",
                    (tenant, agent, task, action, generation, context.policy_revision, subject, lease_seconds),
                ).fetchone()
                if row is not None:
                    self._event(
                        conn, row["operation_id"], "operation.claimed", subject,
                        {"attempt": int(row["attempts"]),
                         "lease_generation": int(row["lease_generation"]),
                         "task_lease_generation": generation, "agent_id": agent},
                        int(row["version"]),
                    )
                return self._row(row)

    def authorize_dispatch(self, context, operation_id: str, *,
                           lease_generation: str, expected_version: int) -> ExternalOperation:
        """Revalidate task lease and delegation immediately before provider dispatch."""
        action = self._validate_text(context.authorized_action, "authorized_action")
        tenant, task, owner, subject, agent, generation = self._verified_context(context, action)
        operation = self._validate_text(operation_id, "operation_id", 32)
        if not isinstance(lease_generation, str) or not lease_generation.isdecimal():
            raise ValueError("external_operation_lease_generation_invalid")
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("external_operation_expected_version_invalid")
        with self.pool.connection() as conn:
            with conn.transaction():
                self._lock_authorized_task(conn, context, action_name=action)
                row = conn.execute(
                    """SELECT * FROM external_operations
                       WHERE operation_id=%s AND tenant_id=%s AND task_id=%s
                         AND agent_id=%s AND action_name=%s AND task_lease_generation=%s
                         AND policy_revision=%s AND state='inflight' AND lease_owner=%s
                         AND lease_generation=%s AND version=%s
                         AND lease_expires_at > clock_timestamp()
                       FOR UPDATE""",
                    (operation, tenant, task, agent, action, generation, context.policy_revision, subject,
                     int(lease_generation), expected_version),
                ).fetchone()
                if row is None:
                    raise WorkflowConflict("operation or task authorization fence is no longer current")
                return self._row(row)

    def _finish_authorized(self, context, operation_id: str, *,
                           lease_generation: str, expected_version: int,
                           state: str, result_digest: str | None = None,
                           provider_reference: str | None = None,
                           error_code: str | None = None) -> ExternalOperation:
        action = self._validate_text(context.authorized_action, "authorized_action")
        tenant, task, owner, subject, agent, generation = self._verified_context(context, action)
        operation = self._validate_text(operation_id, "operation_id", 32)
        if not isinstance(lease_generation, str) or not lease_generation.isdecimal():
            raise ValueError("external_operation_lease_generation_invalid")
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("external_operation_expected_version_invalid")
        if state not in {"succeeded", "failed_terminal", "unknown"}:
            raise ValueError("external_operation_terminal_state_invalid")
        if result_digest is not None:
            self._validate_digest(result_digest, "result_digest")
        if provider_reference is not None:
            provider_reference = self._validate_text(provider_reference, "provider_reference", 256)
        if error_code is not None:
            error_code = self._validate_text(error_code, "error_code", 96)
        event_kind = {"succeeded": "operation.succeeded",
                      "failed_terminal": "operation.failed_terminal",
                      "unknown": "operation.unknown"}[state]
        with self.pool.connection() as conn:
            with conn.transaction():
                self._lock_authorized_task(conn, context, action_name=action)
                row = conn.execute(
                    """UPDATE external_operations SET state=%s,result_digest=%s,
                       provider_reference=%s,error_code=%s,lease_owner=NULL,
                       lease_expires_at=NULL,updated_at=clock_timestamp(),version=version+1
                       WHERE operation_id=%s AND tenant_id=%s AND task_id=%s
                         AND agent_id=%s AND action_name=%s AND task_lease_generation=%s
                         AND policy_revision=%s AND state='inflight' AND lease_owner=%s AND lease_generation=%s
                         AND version=%s AND lease_expires_at > clock_timestamp()
                       RETURNING *""",
                    (state, result_digest, provider_reference, error_code,
                     operation, tenant, task, agent, action, generation,
                     context.policy_revision, subject, int(lease_generation), expected_version),
                ).fetchone()
                if row is None:
                    current = conn.execute(
                        "SELECT state,version FROM external_operations WHERE operation_id=%s",
                        (operation,),
                    ).fetchone()
                    if current is None:
                        raise KeyError("external_operation_not_found")
                    raise WorkflowConflict("operation or task authorization fence is no longer current")
                self._event(conn, operation, event_kind, subject,
                            {"result_digest": result_digest,
                             "provider_reference": provider_reference,
                             "error_code": error_code,
                             "authorization_decision_id": context.authorization_decision_id},
                            int(row["version"]))
                return self._row(conn.execute(
                    "SELECT * FROM external_operations WHERE operation_id=%s", (operation,)
                ).fetchone())

    def complete_authorized(self, context, operation_id: str, *,
                            lease_generation: str, expected_version: int,
                            result_digest: str,
                            provider_reference: str | None = None) -> ExternalOperation:
        return self._finish_authorized(
            context, operation_id, lease_generation=lease_generation,
            expected_version=expected_version, state="succeeded",
            result_digest=result_digest, provider_reference=provider_reference,
        )

    def fail_terminal_authorized(self, context, operation_id: str, *,
                                 lease_generation: str, expected_version: int,
                                 error_code: str) -> ExternalOperation:
        return self._finish_authorized(
            context, operation_id, lease_generation=lease_generation,
            expected_version=expected_version, state="failed_terminal",
            error_code=error_code,
        )

    def mark_unknown_authorized(self, context, operation_id: str, *,
                                lease_generation: str, expected_version: int,
                                reason_code: str) -> ExternalOperation:
        return self._finish_authorized(
            context, operation_id, lease_generation=lease_generation,
            expected_version=expected_version, state="unknown",
            error_code=reason_code,
        )

    def get_authorized(self, context, operation_id: str) -> ExternalOperation | None:
        action = self._validate_text(context.authorized_action, "authorized_action")
        tenant, task, owner, subject, agent, generation = self._verified_context(context, action)
        operation = self._validate_text(operation_id, "operation_id", 32)
        with self.pool.connection() as conn:
            with conn.transaction():
                self._lock_authorized_task(conn, context, action_name=action)
                row = conn.execute(
                    """SELECT * FROM external_operations
                       WHERE operation_id=%s AND tenant_id=%s AND task_id=%s
                         AND agent_id=%s AND action_name=%s AND task_lease_generation=%s
                         AND policy_revision=%s""",
                    (operation, tenant, task, agent, action, generation, context.policy_revision),
                ).fetchone()
                return self._row(row)

    def list_events_authorized(self, context, operation_id: str) -> tuple[dict[str, Any], ...]:
        action = self._validate_text(context.authorized_action, "authorized_action")
        tenant, task, owner, subject, agent, generation = self._verified_context(context, action)
        operation = self._validate_text(operation_id, "operation_id", 32)
        with self.pool.connection() as conn:
            with conn.transaction():
                self._lock_authorized_task(conn, context, action_name=action)
                exists = conn.execute(
                    """SELECT 1 FROM external_operations
                       WHERE operation_id=%s AND tenant_id=%s AND task_id=%s
                         AND agent_id=%s AND action_name=%s AND task_lease_generation=%s
                         AND policy_revision=%s""",
                    (operation, tenant, task, agent, action, generation, context.policy_revision),
                ).fetchone()
                if exists is None:
                    return ()
                rows = conn.execute(
                    """SELECT event_id,sequence,kind,actor_id,payload_json,created_at
                       FROM external_operation_events WHERE operation_id=%s ORDER BY sequence""",
                    (operation,),
                ).fetchall()
                return tuple(dict(row) for row in rows)

    def _get_unchecked(self, operation_id: str) -> ExternalOperation | None:
        operation = self._validate_text(operation_id, "operation_id", 32)
        with self.pool.connection() as conn:
            return self._row(conn.execute(
                "SELECT * FROM external_operations WHERE operation_id=%s", (operation,)
            ).fetchone())

    def _claim_next_unscoped(self, *, worker_id: str, lease_seconds: int = 60) -> ExternalOperation | None:
        worker = self._validate_text(worker_id, "worker_id", 200)
        if type(lease_seconds) is not int or not 5 <= lease_seconds <= 3600:
            raise ValueError("external_operation_lease_seconds_invalid")
        with self.pool.connection() as conn:
            with conn.transaction():
                # An expired lease is ambiguous: record unknown, never reclaim it.
                expired = conn.execute(
                    """UPDATE external_operations SET state='unknown', lease_owner=NULL,
                       lease_expires_at=NULL, updated_at=clock_timestamp(), version=version+1
                       WHERE state='inflight' AND lease_expires_at <= clock_timestamp()
                       RETURNING operation_id,version""",
                ).fetchall()
                for item in expired:
                    self._event(conn, item["operation_id"], "operation.unknown", "lease-recovery",
                                {"reason": "lease_expired", "automatic_replay": False},
                                int(item["version"]))
                row = conn.execute(
                    """WITH candidate AS (
                         SELECT operation_id FROM external_operations
                         WHERE state='pending' ORDER BY created_at,operation_id
                         FOR UPDATE SKIP LOCKED LIMIT 1
                       )
                       UPDATE external_operations AS op SET state='inflight',
                         lease_owner=%s,
                         lease_expires_at=clock_timestamp()+(%s * interval '1 second'),
                         lease_generation=op.lease_generation+1, attempts=op.attempts+1,
                         version=op.version+1, updated_at=clock_timestamp()
                       FROM candidate WHERE op.operation_id=candidate.operation_id
                       RETURNING op.*""",
                    (worker, lease_seconds),
                ).fetchone()
                if row is not None:
                    self._event(conn, row["operation_id"], "operation.claimed", worker,
                                {"attempt": int(row["attempts"]),
                                 "lease_generation": int(row["lease_generation"])},
                                int(row["version"]))
                return self._row(row)

    def _finish(self, operation_id: str, *, worker_id: str, lease_generation: str,
                expected_version: int, state: str, actor_id: str,
                result_digest: str | None = None, provider_reference: str | None = None,
                error_code: str | None = None) -> ExternalOperation:
        operation = self._validate_text(operation_id, "operation_id", 32)
        worker = self._validate_text(worker_id, "worker_id", 200)
        actor = self._validate_text(actor_id, "actor_id", 200)
        if not isinstance(lease_generation, str) or not lease_generation.isdecimal():
            raise ValueError("external_operation_lease_generation_invalid")
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("external_operation_expected_version_invalid")
        if state not in {"succeeded", "failed_terminal", "unknown"}:
            raise ValueError("external_operation_terminal_state_invalid")
        if result_digest is not None:
            self._validate_digest(result_digest, "result_digest")
        if provider_reference is not None:
            provider_reference = self._validate_text(provider_reference, "provider_reference", 256)
        if error_code is not None:
            error_code = self._validate_text(error_code, "error_code", 96)
        event_kind = {
            "succeeded": "operation.succeeded",
            "failed_terminal": "operation.failed_terminal",
            "unknown": "operation.unknown",
        }[state]
        with self.pool.connection() as conn:
            with conn.transaction():
                row = conn.execute(
                    """UPDATE external_operations SET state=%s, result_digest=%s,
                       provider_reference=%s,error_code=%s,lease_owner=NULL,
                       lease_expires_at=NULL,updated_at=clock_timestamp(),version=version+1
                       WHERE operation_id=%s AND state='inflight' AND lease_owner=%s
                         AND lease_generation=%s AND version=%s
                         AND lease_expires_at > clock_timestamp()
                       RETURNING *""",
                    (state, result_digest, provider_reference, error_code,
                     operation, worker, int(lease_generation), expected_version),
                ).fetchone()
                if row is None:
                    current = conn.execute(
                        "SELECT state FROM external_operations WHERE operation_id=%s",
                        (operation,),
                    ).fetchone()
                    if current is None:
                        raise KeyError("external_operation_not_found")
                    raise WorkflowConflict("external operation lease or version is no longer current")
                payload = {"result_digest": result_digest, "provider_reference": provider_reference,
                           "error_code": error_code}
                self._event(conn, operation, event_kind, actor, payload, int(row["version"]))
                return self._row(conn.execute(
                    "SELECT * FROM external_operations WHERE operation_id=%s", (operation,)
                ).fetchone())

    def _complete_unchecked(self, operation_id: str, *, worker_id: str, lease_generation: str,
                 expected_version: int, result_digest: str,
                 provider_reference: str | None = None) -> ExternalOperation:
        return self._finish(
            operation_id, worker_id=worker_id, lease_generation=lease_generation,
            expected_version=expected_version, state="succeeded", actor_id=worker_id,
            result_digest=result_digest, provider_reference=provider_reference,
        )

    def _fail_terminal_unchecked(self, operation_id: str, *, worker_id: str, lease_generation: str,
                      expected_version: int, error_code: str) -> ExternalOperation:
        return self._finish(
            operation_id, worker_id=worker_id, lease_generation=lease_generation,
            expected_version=expected_version, state="failed_terminal", actor_id=worker_id,
            error_code=error_code,
        )

    def _mark_unknown_unchecked(self, operation_id: str, *, worker_id: str, lease_generation: str,
                     expected_version: int, reason_code: str) -> ExternalOperation:
        return self._finish(
            operation_id, worker_id=worker_id, lease_generation=lease_generation,
            expected_version=expected_version, state="unknown", actor_id=worker_id,
            error_code=reason_code,
        )

    def record_reconciliation(self, operation_id: str, *, actor_id: str,
                              expected_version: int, outcome: str,
                              result_digest: str | None = None,
                              provider_reference: str | None = None,
                              reason_code: str | None = None) -> ExternalOperation:
        """Record an explicitly authorized external reconciliation decision.

        Authorization is intentionally required from the caller before this
        method is invoked; this store records actor and outcome atomically.
        Reconciliation never returns an operation to pending.
        """
        operation = self._validate_text(operation_id, "operation_id", 32)
        actor = self._validate_text(actor_id, "actor_id", 200)
        try:
            allowed = self.authorize_reconciliation(actor, operation, expected_version)
        except Exception as exc:
            raise PermissionError("external_operation_reconciliation_authorization_unavailable") from exc
        if allowed is not True:
            raise PermissionError("external_operation_reconciliation_forbidden")
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("external_operation_expected_version_invalid")
        if outcome not in {"succeeded", "failed_terminal", "reconciliation_required"}:
            raise ValueError("external_operation_reconciliation_outcome_invalid")
        if result_digest is not None:
            self._validate_digest(result_digest, "result_digest")
        if provider_reference is not None:
            provider_reference = self._validate_text(provider_reference, "provider_reference", 256)
        if reason_code is not None:
            reason_code = self._validate_text(reason_code, "reason_code", 96)
        with self.pool.connection() as conn:
            with conn.transaction():
                row = conn.execute(
                    """UPDATE external_operations SET state=%s,result_digest=%s,
                       provider_reference=%s,error_code=%s,updated_at=clock_timestamp(),
                       version=version+1
                       WHERE operation_id=%s AND state IN ('unknown','reconciliation_required')
                         AND version=%s RETURNING *""",
                    (outcome, result_digest, provider_reference, reason_code,
                     operation, expected_version),
                ).fetchone()
                if row is None:
                    current = conn.execute(
                        "SELECT state,version FROM external_operations WHERE operation_id=%s",
                        (operation,),
                    ).fetchone()
                    if current is None:
                        raise KeyError("external_operation_not_found")
                    if int(current["version"]) != expected_version:
                        raise WorkflowConflict("external operation version changed")
                    raise WorkflowTransitionError("operation is not awaiting reconciliation")
                self._event(
                    conn, operation, "operation.reconciled", actor,
                    {"outcome": outcome, "result_digest": result_digest,
                     "provider_reference": provider_reference, "reason_code": reason_code},
                    int(row["version"]),
                )
                return self._row(conn.execute(
                    "SELECT * FROM external_operations WHERE operation_id=%s", (operation,)
                ).fetchone())

    def _list_events_unchecked(self, operation_id: str) -> tuple[dict[str, Any], ...]:
        operation = self._validate_text(operation_id, "operation_id", 32)
        with self.pool.connection() as conn:
            rows = conn.execute(
                """SELECT event_id,sequence,kind,actor_id,payload_json,created_at
                   FROM external_operation_events WHERE operation_id=%s ORDER BY sequence""",
                (operation,),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def check_ready(self) -> bool:
        with self.pool.connection() as conn:
            conn.execute("SELECT operation_id,state,version,agent_id,task_lease_generation,authorization_decision_id,policy_revision FROM external_operations LIMIT 0")
            conn.execute("SELECT event_id,operation_id FROM external_operation_events LIMIT 0")
        return True

    def close(self) -> None:
        self.pool.close()
