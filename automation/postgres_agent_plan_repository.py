"""Shared PostgreSQL repository for tenant-owned agent plans and dispatch intents.

This adapter is deliberately separate from the SQLite coordinator until the
coordinator can use its transaction boundaries end-to-end. Dispatch is
at-least-once; callers must enqueue with a stable TaskRuntime idempotency key.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import secrets
from typing import Any, Iterable, Sequence

from automation.agent_plan_repository import PlanSubtaskRecord

from automation.agent_plan_contract import agent_plan_request_digest
from core.postgres_database_identity import postgres_database_identity
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class AgentPlanRepositoryConflict(RuntimeError):
    """A scoped idempotency or optimistic ownership conflict occurred."""


@dataclass(frozen=True)
class DispatchReservation:
    tenant_id: str
    owner_principal_id: str
    plan_id: str
    subtask_id: str
    reservation_token: str
    generation: int
    expires_at: str
    state: str = "reserved"
    task_id: str | None = None

    @property
    def claim_token(self) -> str | None:
        """Expose the SQLite coordinator contract over the reservation fence."""
        return self.reservation_token if self.state == "reserved" else None


class PostgresAgentPlanRepository:
    """PostgreSQL-backed plan/subtask storage and fenced dispatch outbox."""

    def __init__(self, database_url: str, *, min_pool_size: int = 1,
                 max_pool_size: int = 10, pool_timeout: float = 10.0):
        if not isinstance(database_url, str) or not database_url.strip() or len(database_url) > 4096:
            raise ValueError("agent_plan_database_url_invalid")
        if type(min_pool_size) is not int or type(max_pool_size) is not int or not 1 <= min_pool_size <= max_pool_size <= 50:
            raise ValueError("agent_plan_pool_size_invalid")
        if isinstance(pool_timeout, bool) or not isinstance(pool_timeout, (int, float)) or not 1 <= pool_timeout <= 120:
            raise ValueError("agent_plan_pool_timeout_invalid")
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
    def _text(value: Any, name: str, maximum: int = 200) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\x00" in value:
            raise ValueError(name + "_invalid")
        value = value.strip()
        if name != "description" and not _SAFE.fullmatch(value):
            raise ValueError(name + "_invalid")
        return value

    @staticmethod
    def _json_object(value: Any, name: str) -> dict:
        if not isinstance(value, dict):
            raise ValueError(name + "_must_be_object")
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(encoded.encode("utf-8")) > 24 * 1024:
            raise ValueError(name + "_too_large")
        return json.loads(encoded)

    def check_ready(self) -> bool:
        required_columns = {
            "agent_plans": {
                "plan_id", "tenant_id", "owner_principal_id",
                "client_idempotency_key", "request_digest", "created_at", "updated_at",
            },
            "agent_plan_subtasks": {
                "tenant_id", "owner_principal_id", "plan_id", "subtask_id",
                "agent_id", "action", "description", "capabilities_json", "permissions_json",
                "dependencies_json", "requires_approval", "task_id", "status",
                "dispatch_generation", "created_at", "updated_at",
            },
            "agent_plan_dispatch_reservations": {
                "tenant_id", "owner_principal_id", "plan_id", "subtask_id",
                "reservation_token", "dispatch_generation", "state", "reserved_at",
                "expires_at", "task_id",
            },
            "agent_plan_dispatch_outbox": {
                "envelope_version", "envelope_json", "envelope_digest",
                "outbox_id", "tenant_id", "owner_principal_id", "plan_id",
                "subtask_id", "dispatch_generation", "task_id", "state", "attempts",
                "available_at", "created_at", "updated_at", "claim_token", "claim_generation",
            },
        }
        required_constraints = {
            "agent_plans_tenant_owner_plan_unique",
            "agent_plans_scoped_idempotency_unique",
            "agent_plan_subtasks_plan_owner_fk",
            "agent_plan_subtasks_task_owner_fk",
            "agent_plan_subtasks_status_check",
            "agent_plan_subtasks_action_check",
            "agent_plan_subtasks_generation_check",
            "agent_plan_reservation_subtask_fk",
            "agent_plan_reservation_state_check",
            "agent_plan_reservation_generation_check",
            "agent_plan_reservation_expiry_check",
            "agent_plan_outbox_subtask_fk",
            "agent_plan_outbox_generation_unique",
            "agent_plan_outbox_state_check",
            "agent_plan_outbox_attempts_check",
            "agent_plan_outbox_claim_generation_check",
            "agent_plan_outbox_claim_token_check",
            "agent_plan_outbox_envelope_all_or_none",
        }
        required_indexes = {
            "agent_plans_owner_created_idx",
            "agent_plan_subtasks_dispatch_idx",
            "agent_plan_reservations_expiry_idx",
            "agent_plan_dispatch_outbox_ready_idx",
        }
        with self.pool.connection() as conn:
            column_rows = conn.execute(
                "SELECT table_name,column_name FROM information_schema.columns "
                "WHERE table_schema=current_schema() AND table_name = ANY(%s)",
                (list(required_columns),),
            ).fetchall()
            constraint_rows = conn.execute(
                "SELECT conname FROM pg_constraint "
                "WHERE connamespace=current_schema()::regnamespace AND conname = ANY(%s)",
                (list(required_constraints),),
            ).fetchall()
            index_rows = conn.execute(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname=current_schema() AND indexname = ANY(%s)",
                (list(required_indexes),),
            ).fetchall()

        found_columns: dict[str, set[str]] = {}
        for row in column_rows:
            found_columns.setdefault(row["table_name"], set()).add(row["column_name"])
        missing_columns = {
            table: sorted(columns - found_columns.get(table, set()))
            for table, columns in required_columns.items()
            if columns - found_columns.get(table, set())
        }
        found_constraints = {row["conname"] for row in constraint_rows}
        found_indexes = {row["indexname"] for row in index_rows}
        missing_constraints = sorted(required_constraints - found_constraints)
        missing_indexes = sorted(required_indexes - found_indexes)
        if missing_columns or missing_constraints or missing_indexes:
            # Do not leak schema names or database metadata into caller-facing errors.
            raise RuntimeError("agent_plan_repository_schema_incomplete")
        return True

    def create_plan(self, *, tenant_id: str, owner_principal_id: str,
                    client_idempotency_key: str, request_digest: str,
                    subtasks: Iterable[dict]) -> dict:
        tenant = self._text(tenant_id, "tenant_id")
        owner = self._text(owner_principal_id, "owner_principal_id")
        key = self._text(client_idempotency_key, "client_idempotency_key")
        if not isinstance(request_digest, str) or not _HEX64.fullmatch(request_digest):
            raise ValueError("request_digest_invalid")
        items = tuple(subtasks)
        if not 1 <= len(items) <= 20:
            raise ValueError("agent_plan_subtasks_invalid")
        normalized = []
        seen = set()
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("agent_plan_subtask_invalid")
            subtask = self._text(item.get("subtask_id"), "subtask_id", 80)
            if subtask in seen:
                raise ValueError("agent_plan_subtask_duplicate")
            seen.add(subtask)
            normalized.append({
                "subtask_id": subtask,
                "agent_id": self._text(item.get("agent_id"), "agent_id", 80),
                "action": self._text(item.get("action", "emit"), "action", 80),
                "description": self._text(item.get("description"), "description", 2000),
                "capabilities": item.get("capabilities", []),
                "permissions": item.get("permissions", []),
                "dependencies": item.get("dependencies", []),
                "requires_approval": item.get("requires_approval", True),
            })
            for field in ("capabilities", "permissions", "dependencies"):
                values = normalized[-1][field]
                if (not isinstance(values, list) or len(values) > 32
                        or any(not isinstance(value, str) or not _SAFE.fullmatch(value) for value in values)
                        or len(set(values)) != len(values)):
                    raise ValueError("agent_plan_subtask_" + field + "_invalid")
            if type(normalized[-1]["requires_approval"]) is not bool:
                raise ValueError("agent_plan_subtask_approval_invalid")
        computed_digest = agent_plan_request_digest(
            tenant_id=tenant, owner_principal_id=owner,
            subtasks=[{
                "subtask_id": item["subtask_id"],
                "agent_id": item["agent_id"],
                "action": item["action"],
                "description": item["description"],
                "required_capabilities": item["capabilities"],
                "required_permissions": item["permissions"],
                "depends_on": item["dependencies"],
                "requires_approval": item["requires_approval"],
            } for item in normalized],
        )
        if request_digest != computed_digest:
            raise ValueError("request_digest_does_not_match_plan_payload")
        plan_id = "ap_" + hashlib.sha256(
            (tenant + "\\0" + owner + "\\0" + key).encode("utf-8")
        ).hexdigest()
        with self.pool.connection() as conn:
            with conn.transaction():
                inserted = conn.execute(
                    """INSERT INTO agent_plans
                       (plan_id,tenant_id,owner_principal_id,client_idempotency_key,request_digest)
                       VALUES (%s,%s,%s,%s,%s)
                       ON CONFLICT (tenant_id,owner_principal_id,client_idempotency_key) DO NOTHING
                       RETURNING plan_id""",
                    (plan_id, tenant, owner, key, request_digest),
                ).fetchone()
                if inserted is None:
                    existing = conn.execute(
                        "SELECT * FROM agent_plans WHERE tenant_id=%s AND owner_principal_id=%s "
                        "AND client_idempotency_key=%s FOR UPDATE",
                        (tenant, owner, key),
                    ).fetchone()
                    if existing is None:
                        raise AgentPlanRepositoryConflict("agent_plan_idempotency_race_unresolved")
                    if existing["request_digest"] != request_digest:
                        raise AgentPlanRepositoryConflict("agent_plan_idempotency_payload_conflict")
                    return self._load_in_transaction(conn, tenant, owner, existing["plan_id"])
                for item in normalized:
                    conn.execute(
                        """INSERT INTO agent_plan_subtasks
                           (tenant_id,owner_principal_id,plan_id,subtask_id,agent_id,action,description,
                            capabilities_json,permissions_json,dependencies_json,requires_approval)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (tenant, owner, plan_id, item["subtask_id"], item["agent_id"], item["action"],
                         item["description"], Jsonb(item["capabilities"]),
                         Jsonb(item["permissions"]), Jsonb(item["dependencies"]),
                         item["requires_approval"]),
                    )
                return self._load_in_transaction(conn, tenant, owner, plan_id)

    @staticmethod
    def _load_in_transaction(conn, tenant: str, owner: str, plan_id: str) -> dict:
        plan = conn.execute(
            "SELECT * FROM agent_plans WHERE tenant_id=%s AND owner_principal_id=%s AND plan_id=%s",
            (tenant, owner, plan_id),
        ).fetchone()
        if plan is None:
            raise KeyError("agent_plan_not_found")
        subtasks = conn.execute(
            "SELECT * FROM agent_plan_subtasks WHERE tenant_id=%s AND owner_principal_id=%s "
            "AND plan_id=%s ORDER BY subtask_id",
            (tenant, owner, plan_id),
        ).fetchall()
        return {"plan": plan, "subtasks": subtasks}

    def get_plan(self, *, tenant_id: str, owner_principal_id: str, plan_id: str) -> dict | None:
        """Return an owned plan, using None for both absent and foreign-owned IDs."""
        tenant = self._text(tenant_id, "tenant_id")
        owner = self._text(owner_principal_id, "owner_principal_id")
        plan = self._text(plan_id, "plan_id", 128)
        with self.pool.connection() as conn:
            with conn.transaction():
                row = conn.execute(
                    "SELECT plan_id FROM agent_plans "
                    "WHERE tenant_id=%s AND owner_principal_id=%s AND plan_id=%s",
                    (tenant, owner, plan),
                ).fetchone()
                if row is None:
                    return None
                return self._load_in_transaction(conn, tenant, owner, plan)

    def list_subtasks(
        self, *, plan_id: str, tenant_id: str, owner_principal_id: str,
    ) -> tuple[PlanSubtaskRecord, ...] | None:
        """Return typed subtasks for an owned plan; hide absent and foreign plans."""
        tenant = self._text(tenant_id, "tenant_id")
        owner = self._text(owner_principal_id, "owner_principal_id")
        plan = self._text(plan_id, "plan_id", 128)
        with self.pool.connection() as conn:
            with conn.transaction():
                owned = conn.execute(
                    "SELECT 1 FROM agent_plans WHERE tenant_id=%s "
                    "AND owner_principal_id=%s AND plan_id=%s",
                    (tenant, owner, plan),
                ).fetchone()
                if owned is None:
                    return None
                rows = conn.execute(
                    "SELECT * FROM agent_plan_subtasks WHERE tenant_id=%s "
                    "AND owner_principal_id=%s AND plan_id=%s ORDER BY subtask_id",
                    (tenant, owner, plan),
                ).fetchall()
                return tuple(PlanSubtaskRecord(
                    subtask_id=row["subtask_id"],
                    agent_id=row["agent_id"],
                    action=row["action"],
                    description=row["description"],
                    required_capabilities=tuple(row["capabilities_json"]),
                    required_permissions=tuple(row["permissions_json"]),
                    depends_on=tuple(row["dependencies_json"]),
                    requires_approval=row["requires_approval"],
                    task_id=row["task_id"],
                    status=row["status"],
                ) for row in rows)

    def update_subtask(
        self, *, plan_id: str, subtask_id: str, tenant_id: str,
        owner_principal_id: str, status: str, task_id: str | None = None,
        expected_statuses: Sequence[str] | None = None,
    ) -> bool:
        """Update one owned subtask with optional status compare-and-set."""
        tenant = self._text(tenant_id, "tenant_id")
        owner = self._text(owner_principal_id, "owner_principal_id")
        plan = self._text(plan_id, "plan_id", 128)
        subtask = self._text(subtask_id, "subtask_id", 80)
        if not isinstance(status, str) or not status.strip() or len(status) > 40:
            raise ValueError("agent_plan_subtask_status_invalid")
        if task_id is not None:
            task_id = self._text(task_id, "task_id")
        predicates = [
            "tenant_id=%s", "owner_principal_id=%s", "plan_id=%s", "subtask_id=%s",
        ]
        params: list[Any] = [tenant, owner, plan, subtask]
        if expected_statuses is not None:
            values = tuple(expected_statuses)
            if not values:
                return False
            if any(not isinstance(value, str) or not value.strip() for value in values):
                raise ValueError("agent_plan_subtask_expected_statuses_invalid")
            predicates.append("status = ANY(%s)")
            params.append(list(values))
        if task_id is None:
            statement = (
                "UPDATE agent_plan_subtasks SET status=%s,updated_at=clock_timestamp() WHERE "
                + " AND ".join(predicates)
            )
            params.insert(0, status)
        else:
            statement = (
                "UPDATE agent_plan_subtasks SET task_id=%s,status=%s,updated_at=clock_timestamp() WHERE "
                + " AND ".join(predicates)
            )
            params[0:0] = [task_id, status]
        with self.pool.connection() as conn:
            with conn.transaction():
                result = conn.execute(statement, params)
                return result.rowcount == 1

    def reserve_dispatch(self, *, tenant_id: str, owner_principal_id: str,
                         plan_id: str, subtask_id: str, ttl_seconds: int = 60) -> DispatchReservation | None:
        tenant = self._text(tenant_id, "tenant_id")
        owner = self._text(owner_principal_id, "owner_principal_id")
        plan = self._text(plan_id, "plan_id", 128)
        subtask = self._text(subtask_id, "subtask_id", 80)
        if type(ttl_seconds) is not int or not 5 <= ttl_seconds <= 900:
            raise ValueError("dispatch_reservation_ttl_invalid")
        token = secrets.token_urlsafe(32)
        with self.pool.connection() as conn:
            with conn.transaction():
                row = conn.execute(
                    """SELECT status,dispatch_generation FROM agent_plan_subtasks
                       WHERE tenant_id=%s AND owner_principal_id=%s AND plan_id=%s AND subtask_id=%s
                       FOR UPDATE""",
                    (tenant, owner, plan, subtask),
                ).fetchone()
                if row is None:
                    raise KeyError("agent_plan_subtask_not_found")
                current = conn.execute(
                    """SELECT state,reservation_token,dispatch_generation,expires_at,task_id
                       FROM agent_plan_dispatch_reservations
                       WHERE tenant_id=%s AND owner_principal_id=%s AND plan_id=%s AND subtask_id=%s
                       FOR UPDATE""",
                    (tenant, owner, plan, subtask),
                ).fetchone()
                if row["status"] == "cancelled":
                    if current is None or current["state"] != "cancelled":
                        raise AgentPlanRepositoryConflict("agent_plan_cancel_tombstone_missing")
                    return DispatchReservation(
                        tenant, owner, plan, subtask, current["reservation_token"],
                        int(current["dispatch_generation"]), current["expires_at"].isoformat(),
                        "cancelled", current["task_id"],
                    )
                if row["status"] not in ("pending", "reserved"):
                    return None
                if current and current["state"] == "cancelled":
                    return DispatchReservation(
                        tenant, owner, plan, subtask, current["reservation_token"],
                        int(current["dispatch_generation"]), current["expires_at"].isoformat(),
                        "cancelled", current["task_id"],
                    )
                if current and current["state"] == "reserved":
                    alive = conn.execute(
                        "SELECT %s > clock_timestamp() AS alive", (current["expires_at"],)
                    ).fetchone()["alive"]
                    if alive:
                        return None
                generation = int(row["dispatch_generation"]) + 1
                reservation = conn.execute(
                    """INSERT INTO agent_plan_dispatch_reservations
                       (tenant_id,owner_principal_id,plan_id,subtask_id,reservation_token,
                        dispatch_generation,state,reserved_at,expires_at)
                       VALUES (%s,%s,%s,%s,%s,%s,'reserved',clock_timestamp(),
                               clock_timestamp() + (%s * interval '1 second'))
                       ON CONFLICT (tenant_id,owner_principal_id,plan_id,subtask_id)
                       DO UPDATE SET reservation_token=EXCLUDED.reservation_token,
                         dispatch_generation=EXCLUDED.dispatch_generation,state='reserved',
                         reserved_at=clock_timestamp(),expires_at=EXCLUDED.expires_at,task_id=NULL
                       RETURNING reservation_token,dispatch_generation,expires_at""",
                    (tenant, owner, plan, subtask, token, generation, ttl_seconds),
                ).fetchone()
                conn.execute(
                    "UPDATE agent_plan_subtasks SET status='reserved',dispatch_generation=%s,updated_at=clock_timestamp() "
                    "WHERE tenant_id=%s AND owner_principal_id=%s AND plan_id=%s AND subtask_id=%s",
                    (generation, tenant, owner, plan, subtask),
                )
                conn.execute(
                    """INSERT INTO agent_plan_dispatch_outbox
                       (tenant_id,owner_principal_id,plan_id,subtask_id,dispatch_generation,state)
                       VALUES (%s,%s,%s,%s,%s,'pending')""",
                    (tenant, owner, plan, subtask, generation),
                )
                return DispatchReservation(
                    tenant, owner, plan, subtask, reservation["reservation_token"],
                    int(reservation["dispatch_generation"]), reservation["expires_at"].isoformat(),
                    "reserved", None,
                )

    def complete_dispatch(
        self, *, tenant_id: str, owner_principal_id: str, plan_id: str,
        subtask_id: str, claim_token: str, generation: int, task_id: str,
        status: str,
    ) -> bool:
        """Atomically consume the reservation fence, bind the task and deliver its outbox row."""
        tenant = self._text(tenant_id, "tenant_id")
        owner = self._text(owner_principal_id, "owner_principal_id")
        plan = self._text(plan_id, "plan_id", 128)
        subtask = self._text(subtask_id, "subtask_id", 80)
        token = self._text(claim_token, "claim_token", 200)
        task = self._text(task_id, "task_id", 200)
        if len(token) < 32 or not isinstance(status, str) or not status.strip() or len(status) > 40:
            raise ValueError("agent_plan_dispatch_completion_invalid")
        if type(generation) is not int or generation < 1:
            raise ValueError("dispatch_generation_invalid")
        with self.pool.connection() as conn:
            with conn.transaction():
                updated = conn.execute(
                    """UPDATE agent_plan_dispatch_reservations
                       SET state='enqueued',task_id=%s
                       WHERE tenant_id=%s AND owner_principal_id=%s AND plan_id=%s
                         AND subtask_id=%s AND reservation_token=%s
                         AND dispatch_generation=%s AND state='reserved'
                         AND expires_at > clock_timestamp()""",
                    (task, tenant, owner, plan, subtask, token, generation),
                )
                if updated.rowcount != 1:
                    return False
                bound = conn.execute(
                    """UPDATE agent_plan_subtasks
                       SET task_id=%s,status=%s,updated_at=clock_timestamp()
                       WHERE tenant_id=%s AND owner_principal_id=%s AND plan_id=%s
                         AND subtask_id=%s AND dispatch_generation=%s""",
                    (task, status, tenant, owner, plan, subtask, generation),
                )
                if bound.rowcount != 1:
                    raise AgentPlanRepositoryConflict("agent_plan_subtask_generation_changed")
                delivered = conn.execute(
                    """UPDATE agent_plan_dispatch_outbox
                       SET state='delivered',task_id=%s,claim_token=NULL,updated_at=clock_timestamp()
                       WHERE tenant_id=%s AND owner_principal_id=%s AND plan_id=%s
                         AND subtask_id=%s AND dispatch_generation=%s
                         AND state IN ('pending','processing')""",
                    (task, tenant, owner, plan, subtask, generation),
                )
                if delivered.rowcount != 1:
                    raise AgentPlanRepositoryConflict("agent_plan_dispatch_outbox_generation_missing")
                return True

    def cancel_dispatch(
        self, *, tenant_id: str, owner_principal_id: str, plan_id: str,
        subtask_id: str,
    ) -> bool:
        """Persist a cancellation tombstone and invalidate every in-flight fence."""
        tenant = self._text(tenant_id, "tenant_id")
        owner = self._text(owner_principal_id, "owner_principal_id")
        plan = self._text(plan_id, "plan_id", 128)
        subtask = self._text(subtask_id, "subtask_id", 80)
        tombstone_token = secrets.token_urlsafe(32)
        with self.pool.connection() as conn:
            with conn.transaction():
                row = conn.execute(
                    """SELECT s.status,s.dispatch_generation
                       FROM agent_plan_subtasks s JOIN agent_plans p
                         ON p.tenant_id=s.tenant_id AND p.owner_principal_id=s.owner_principal_id
                        AND p.plan_id=s.plan_id
                       WHERE s.tenant_id=%s AND s.owner_principal_id=%s
                         AND s.plan_id=%s AND s.subtask_id=%s
                       FOR UPDATE OF s""",
                    (tenant, owner, plan, subtask),
                ).fetchone()
                if row is None:
                    return False
                current = conn.execute(
                    """SELECT state,dispatch_generation FROM agent_plan_dispatch_reservations
                       WHERE tenant_id=%s AND owner_principal_id=%s AND plan_id=%s AND subtask_id=%s
                       FOR UPDATE""",
                    (tenant, owner, plan, subtask),
                ).fetchone()
                # Terminal runtime outcomes must not be rewritten as cancellation,
                # even if a legacy/reconciled reservation is no longer enqueued.
                if row["status"] in ("succeeded", "failed"):
                    return False
                if current and current["state"] == "enqueued":
                    return False
                if current and current["state"] == "cancelled":
                    return True
                generation = int(row["dispatch_generation"]) + 1
                conn.execute(
                    """INSERT INTO agent_plan_dispatch_reservations
                       (tenant_id,owner_principal_id,plan_id,subtask_id,reservation_token,
                        dispatch_generation,state,reserved_at,expires_at,task_id)
                       VALUES (%s,%s,%s,%s,%s,%s,'cancelled',clock_timestamp(),
                               clock_timestamp()+interval '5 minutes',NULL)
                       ON CONFLICT (tenant_id,owner_principal_id,plan_id,subtask_id)
                       DO UPDATE SET reservation_token=EXCLUDED.reservation_token,
                         dispatch_generation=EXCLUDED.dispatch_generation,state='cancelled',
                         reserved_at=clock_timestamp(),expires_at=EXCLUDED.expires_at,task_id=NULL""",
                    (tenant, owner, plan, subtask, tombstone_token, generation),
                )
                conn.execute(
                    """UPDATE agent_plan_subtasks SET dispatch_generation=%s,
                         updated_at=clock_timestamp()
                       WHERE tenant_id=%s AND owner_principal_id=%s AND plan_id=%s AND subtask_id=%s""",
                    (generation, tenant, owner, plan, subtask),
                )
                conn.execute(
                    """UPDATE agent_plan_dispatch_outbox SET state='dead_letter',
                         claim_token=NULL,updated_at=clock_timestamp()
                       WHERE tenant_id=%s AND owner_principal_id=%s AND plan_id=%s
                         AND subtask_id=%s AND state IN ('pending','processing')""",
                    (tenant, owner, plan, subtask),
                )
                return True

    def get_dispatch(
        self, *, tenant_id: str, owner_principal_id: str, plan_id: str,
        subtask_id: str,
    ) -> DispatchReservation | None:
        tenant = self._text(tenant_id, "tenant_id")
        owner = self._text(owner_principal_id, "owner_principal_id")
        plan = self._text(plan_id, "plan_id", 128)
        subtask = self._text(subtask_id, "subtask_id", 80)
        with self.pool.connection() as conn:
            row = conn.execute(
                """SELECT reservation_token,dispatch_generation,state,expires_at,task_id
                   FROM agent_plan_dispatch_reservations
                   WHERE tenant_id=%s AND owner_principal_id=%s AND plan_id=%s AND subtask_id=%s""",
                (tenant, owner, plan, subtask),
            ).fetchone()
            if row is None:
                return None
            return DispatchReservation(
                tenant, owner, plan, subtask, row["reservation_token"],
                int(row["dispatch_generation"]), row["expires_at"].isoformat(),
                row["state"], row["task_id"],
            )

    def attach_dispatch_envelope(
        self, *, tenant_id: str, owner_principal_id: str, plan_id: str,
        subtask_id: str, reservation_token: str, generation: int,
        envelope: dict,
    ) -> str:
        """Persist a canonical immutable execution envelope before an outbox claim.

        The reservation and outbox generation must still be current. The
        application verifies the digest again when loading the envelope; this
        method never accepts worker identity or authorization from the payload.
        """
        tenant = self._text(tenant_id, "tenant_id")
        owner = self._text(owner_principal_id, "owner_principal_id")
        plan = self._text(plan_id, "plan_id", 128)
        subtask = self._text(subtask_id, "subtask_id", 80)
        token = self._text(reservation_token, "reservation_token", 200)
        if len(token) < 32 or type(generation) is not int or generation < 1:
            raise ValueError("dispatch_envelope_reservation_invalid")
        if not isinstance(envelope, dict):
            raise ValueError("dispatch_envelope_invalid")
        required = {
            "version", "tenant_id", "owner_principal_id", "plan_id", "subtask_id",
            "reservation_generation", "idempotency_key", "agent_id", "action",
            "manifest_revision", "required_capabilities", "required_permissions",
            "requires_approval", "dependency_snapshot", "dependency_output_digests",
            "argument",
        }
        if set(envelope) != required or type(envelope.get("version")) is not int or envelope.get("version") != 1:
            raise ValueError("dispatch_envelope_schema_invalid")
        if (
            envelope["tenant_id"] != tenant or envelope["owner_principal_id"] != owner
            or envelope["plan_id"] != plan or envelope["subtask_id"] != subtask
            or envelope["reservation_generation"] != generation
        ):
            raise ValueError("dispatch_envelope_scope_mismatch")
        for key in ("idempotency_key", "agent_id", "action"):
            self._text(envelope[key], "dispatch_envelope_" + key, 200)
        if type(envelope["manifest_revision"]) is not int or envelope["manifest_revision"] < 1:
            raise ValueError("dispatch_envelope_manifest_revision_invalid")
        if type(envelope["requires_approval"]) is not bool:
            raise ValueError("dispatch_envelope_approval_invalid")
        for key in ("required_capabilities", "required_permissions"):
            values = envelope[key]
            if (
                not isinstance(values, list) or len(values) > 32
                or any(not isinstance(value, str) or not _SAFE.fullmatch(value) for value in values)
                or len(set(values)) != len(values)
            ):
                raise ValueError("dispatch_envelope_" + key + "_invalid")
        snapshot = envelope["dependency_snapshot"]
        if not isinstance(snapshot, list) or len(snapshot) > 20:
            raise ValueError("dispatch_envelope_dependencies_invalid")
        seen_dependencies = set()
        for item in snapshot:
            if not isinstance(item, dict) or set(item) != {"subtask_id", "task_id", "state"}:
                raise ValueError("dispatch_envelope_dependency_item_invalid")
            dependency_id = self._text(item["subtask_id"], "dependency_subtask_id", 80)
            self._text(item["task_id"], "dependency_task_id", 200)
            state = item["state"]
            if state not in {"succeeded"} or dependency_id in seen_dependencies:
                raise ValueError("dispatch_envelope_dependency_state_invalid")
            seen_dependencies.add(dependency_id)
        output_digests = envelope["dependency_output_digests"]
        if not isinstance(output_digests, dict) or len(output_digests) > 20:
            raise ValueError("dispatch_envelope_output_digests_invalid")
        for dependency_id, digest_value in output_digests.items():
            self._text(dependency_id, "dependency_output_subtask_id", 80)
            if dependency_id not in seen_dependencies or not isinstance(digest_value, str) or not _HEX64.fullmatch(digest_value):
                raise ValueError("dispatch_envelope_output_digest_invalid")
        argument = envelope["argument"]
        if (not isinstance(argument, str) or "\\x00" in argument
                or len(argument.encode("utf-8")) > 8192):
            raise ValueError("dispatch_envelope_argument_invalid")
        canonical = json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        encoded = canonical.encode("utf-8")
        if len(encoded) > 24 * 1024:
            raise ValueError("dispatch_envelope_too_large")
        digest = hashlib.sha256(encoded).hexdigest()
        with self.pool.connection() as conn:
            with conn.transaction():
                updated = conn.execute(
                    """UPDATE agent_plan_dispatch_outbox o
                       SET envelope_version=1,envelope_json=%s,envelope_digest=%s,
                           updated_at=clock_timestamp()
                       WHERE o.tenant_id=%s AND o.owner_principal_id=%s
                         AND o.plan_id=%s AND o.subtask_id=%s
                         AND o.dispatch_generation=%s AND o.state='pending'
                         AND o.envelope_json IS NULL
                         AND EXISTS (
                           SELECT 1 FROM agent_plan_dispatch_reservations r
                           WHERE r.tenant_id=o.tenant_id
                             AND r.owner_principal_id=o.owner_principal_id
                             AND r.plan_id=o.plan_id AND r.subtask_id=o.subtask_id
                             AND r.reservation_token=%s
                             AND r.dispatch_generation=o.dispatch_generation
                             AND r.state='reserved' AND r.expires_at > clock_timestamp()
                         )""",
                    (Jsonb(envelope), digest, tenant, owner, plan, subtask, generation, token),
                )
                if updated.rowcount != 1:
                    raise AgentPlanRepositoryConflict("agent_plan_dispatch_envelope_fenced")
        return digest

    def claim_outbox(self, *, limit: int = 20) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("dispatch_outbox_limit_invalid")
        with self.pool.connection() as conn:
            with conn.transaction():
                # A newer reservation generation fences old pending/processing
                # rows. Retire them before claiming so abandoned generations do
                # not accumulate as permanently unclaimable outbox work.
                conn.execute(
                    """UPDATE agent_plan_dispatch_outbox o
                       SET state='dead_letter',claim_token=NULL,updated_at=clock_timestamp()
                       WHERE o.state IN ('pending','processing')
                         AND NOT EXISTS (
                           SELECT 1 FROM agent_plan_dispatch_reservations r
                           WHERE r.tenant_id=o.tenant_id
                             AND r.owner_principal_id=o.owner_principal_id
                             AND r.plan_id=o.plan_id AND r.subtask_id=o.subtask_id
                             AND r.dispatch_generation=o.dispatch_generation
                             AND r.state='reserved'
                             AND r.expires_at > clock_timestamp()
                         )"""
                )
                rows = conn.execute(
                    """WITH picked AS (
                         SELECT o.outbox_id,r.reservation_token
                         FROM agent_plan_dispatch_outbox o
                         JOIN agent_plan_dispatch_reservations r
                           ON r.tenant_id=o.tenant_id
                          AND r.owner_principal_id=o.owner_principal_id
                          AND r.plan_id=o.plan_id AND r.subtask_id=o.subtask_id
                          AND r.dispatch_generation=o.dispatch_generation
                         WHERE o.state='pending' AND o.available_at <= clock_timestamp()
                           AND o.envelope_version=1 AND o.envelope_json IS NOT NULL
                           AND o.envelope_digest IS NOT NULL
                           AND r.state='reserved' AND r.expires_at > clock_timestamp()
                         ORDER BY o.outbox_id FOR UPDATE OF o SKIP LOCKED LIMIT %s
                       )
                       UPDATE agent_plan_dispatch_outbox o SET state='processing',
                          attempts=o.attempts+1,updated_at=clock_timestamp()
                       FROM picked WHERE o.outbox_id=picked.outbox_id
                       RETURNING o.*,picked.reservation_token""",
                    (limit,),
                ).fetchall()
                # Each retry gets a fresh secret and monotonically increasing generation.
                # The updates remain inside the same transaction as the row claim.
                for row in rows:
                    claim_token = secrets.token_urlsafe(32)
                    claimed = conn.execute(
                        """UPDATE agent_plan_dispatch_outbox
                           SET claim_token=%s,claim_generation=claim_generation+1,
                               updated_at=clock_timestamp()
                           WHERE outbox_id=%s AND state='processing'
                           RETURNING claim_generation""",
                        (claim_token, row["outbox_id"]),
                    ).fetchone()
                    if claimed is None:
                        raise AgentPlanRepositoryConflict("agent_plan_outbox_claim_lost")
                    row["claim_token"] = claim_token
                    row["claim_generation"] = claimed["claim_generation"]
                # Never hand a claimed payload to a worker until its canonical
                # representation matches the immutable digest stored at write time.
                verified_rows = []
                for row in rows:
                    envelope = row.get("envelope_json")
                    try:
                        canonical = json.dumps(
                            envelope, sort_keys=True, separators=(",", ":"),
                            ensure_ascii=False, allow_nan=False,
                        ).encode("utf-8")
                        valid = (
                            row.get("envelope_version") == 1
                            and isinstance(envelope, dict)
                            and len(canonical) <= 24 * 1024
                            and isinstance(row.get("envelope_digest"), str)
                            and _HEX64.fullmatch(row["envelope_digest"]) is not None
                            and secrets.compare_digest(
                                hashlib.sha256(canonical).hexdigest(),
                                row["envelope_digest"],
                            )
                        )
                    except (TypeError, ValueError, OverflowError):
                        valid = False
                    if not valid:
                        conn.execute(
                            """UPDATE agent_plan_dispatch_outbox
                               SET state='dead_letter',claim_token=NULL,
                                   updated_at=clock_timestamp()
                               WHERE outbox_id=%s AND state='processing'
                                 AND claim_token=%s AND claim_generation=%s""",
                            (row["outbox_id"], row["claim_token"], row["claim_generation"]),
                        )
                        continue
                    verified_rows.append(row)
                return verified_rows

    def heartbeat_outbox_claim(
        self, *, tenant_id: str, owner_principal_id: str, plan_id: str,
        subtask_id: str, reservation_token: str, generation: int,
        claim_token: str, claim_generation: int,
    ) -> bool:
        """Extend a processing claim only while both reservation and claim fences remain current."""
        tenant = self._text(tenant_id, "tenant_id")
        owner = self._text(owner_principal_id, "owner_principal_id")
        plan = self._text(plan_id, "plan_id", 128)
        subtask = self._text(subtask_id, "subtask_id", 80)
        reservation = self._text(reservation_token, "reservation_token", 200)
        claim = self._text(claim_token, "claim_token", 200)
        if type(generation) is not int or generation < 1:
            raise ValueError("dispatch_generation_invalid")
        if type(claim_generation) is not int or claim_generation < 1:
            raise ValueError("dispatch_claim_generation_invalid")
        with self.pool.connection() as conn:
            with conn.transaction():
                updated = conn.execute(
                    """UPDATE agent_plan_dispatch_outbox o
                       SET updated_at=clock_timestamp()
                       WHERE o.tenant_id=%s AND o.owner_principal_id=%s
                         AND o.plan_id=%s AND o.subtask_id=%s
                         AND o.dispatch_generation=%s AND o.state='processing'
                         AND o.claim_token=%s AND o.claim_generation=%s
                         AND EXISTS (
                           SELECT 1 FROM agent_plan_dispatch_reservations r
                           WHERE r.tenant_id=o.tenant_id
                             AND r.owner_principal_id=o.owner_principal_id
                             AND r.plan_id=o.plan_id AND r.subtask_id=o.subtask_id
                             AND r.reservation_token=%s
                             AND r.dispatch_generation=o.dispatch_generation
                             AND r.state='reserved' AND r.expires_at > clock_timestamp()
                         )""",
                    (tenant, owner, plan, subtask, generation, claim,
                     claim_generation, reservation),
                )
                return updated.rowcount == 1

    def mark_enqueued(self, *, tenant_id: str, owner_principal_id: str,
                      plan_id: str, subtask_id: str, reservation_token: str,
                      generation: int, claim_token: str, claim_generation: int, task_id: str) -> bool:
        tenant = self._text(tenant_id, "tenant_id")
        owner = self._text(owner_principal_id, "owner_principal_id")
        plan = self._text(plan_id, "plan_id", 128)
        subtask = self._text(subtask_id, "subtask_id", 80)
        token = self._text(reservation_token, "reservation_token", 200)
        task = self._text(task_id, "task_id", 200)
        claim = self._text(claim_token, "claim_token", 200)
        if type(claim_generation) is not int or claim_generation < 1:
            raise ValueError("dispatch_claim_generation_invalid")
        if type(generation) is not int or generation < 1:
            raise ValueError("dispatch_generation_invalid")
        with self.pool.connection() as conn:
            with conn.transaction():
                updated = conn.execute(
                    """UPDATE agent_plan_dispatch_reservations
                       SET state='enqueued',task_id=%s
                       WHERE tenant_id=%s AND owner_principal_id=%s AND plan_id=%s AND subtask_id=%s
                         AND reservation_token=%s AND dispatch_generation=%s AND state='reserved'
                         AND expires_at > clock_timestamp()""",
                    (task, tenant, owner, plan, subtask, token, generation),
                )
                if updated.rowcount != 1:
                    return False
                subtask_update = conn.execute(
                    """UPDATE agent_plan_subtasks SET task_id=%s,status='queued',updated_at=clock_timestamp()
                       WHERE tenant_id=%s AND owner_principal_id=%s AND plan_id=%s AND subtask_id=%s
                         AND dispatch_generation=%s""",
                     (task, tenant, owner, plan, subtask, generation),
                )
                if subtask_update.rowcount != 1:
                    raise AgentPlanRepositoryConflict("agent_plan_subtask_generation_changed")
                outbox_update = conn.execute(
                    """UPDATE agent_plan_dispatch_outbox SET state='delivered',task_id=%s,claim_token=NULL,updated_at=clock_timestamp()
                       WHERE tenant_id=%s AND owner_principal_id=%s AND plan_id=%s AND subtask_id=%s
                         AND dispatch_generation=%s AND state='processing'
                         AND claim_token=%s AND claim_generation=%s""",
                     (task, tenant, owner, plan, subtask, generation, claim, claim_generation),
                )
                if outbox_update.rowcount != 1:
                    raise AgentPlanRepositoryConflict("agent_plan_dispatch_claim_fenced")
                return True

    def recover_stale_outbox(self, *, stale_after_seconds: int = 60,
                             max_attempts: int = 8, limit: int = 100) -> dict:
        """Return abandoned processing rows to pending or dead-letter them.

        updated_at is the processing lease heartbeat. A worker must call
        claim_outbox again after recovery; old workers cannot finalize a row
        because mark_enqueued is fenced by the reservation token/generation and per-claim token/generation.
        """
        if type(stale_after_seconds) is not int or not 5 <= stale_after_seconds <= 3600:
            raise ValueError("dispatch_outbox_stale_after_invalid")
        if type(max_attempts) is not int or not 1 <= max_attempts <= 100:
            raise ValueError("dispatch_outbox_max_attempts_invalid")
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("dispatch_outbox_recovery_limit_invalid")
        with self.pool.connection() as conn:
            with conn.transaction():
                rows = conn.execute(
                    """WITH stale AS (
                         SELECT outbox_id, attempts FROM agent_plan_dispatch_outbox
                         WHERE state='processing'
                           AND updated_at < clock_timestamp() - (%s * interval '1 second')
                         ORDER BY updated_at, outbox_id
                         FOR UPDATE SKIP LOCKED LIMIT %s
                       )
                       UPDATE agent_plan_dispatch_outbox o
                       SET state=CASE WHEN stale.attempts >= %s THEN 'dead_letter' ELSE 'pending' END,
                           claim_token=NULL,
                           available_at=CASE
                             WHEN stale.attempts >= %s THEN o.available_at
                             ELSE clock_timestamp()
                               + (LEAST(300, (2 ^ LEAST(stale.attempts, 8)))::double precision * interval '1 second')
                           END,
                           updated_at=clock_timestamp()
                       FROM stale WHERE o.outbox_id=stale.outbox_id
                       RETURNING o.state""",
                    (stale_after_seconds, limit, max_attempts, max_attempts),
                ).fetchall()
                return {
                    "requeued": sum(1 for row in rows if row["state"] == "pending"),
                    "dead_lettered": sum(1 for row in rows if row["state"] == "dead_letter"),
                }

    def close(self) -> None:
        self.pool.close()
