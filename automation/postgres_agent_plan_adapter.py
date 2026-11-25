"""Typed adapter from the coordinator plan port to PostgreSQL persistence.

The raw PostgreSQL repository intentionally exposes its outbox worker API
separately. This adapter translates only the coordinator aggregate contract.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Sequence

from automation.agent_plan_repository import (
    AgentPlanRecord,
    DispatchReservation as PlanDispatchReservation,
    PlanSubtaskRecord,
)
from automation.postgres_agent_plan_repository import PostgresAgentPlanRepository


class PostgresAgentPlanRepositoryAdapter:
    """Implement AgentPlanRepository without weakening PostgreSQL fencing."""

    def __init__(self, repository: PostgresAgentPlanRepository):
        if not isinstance(repository, PostgresAgentPlanRepository):
            raise TypeError("postgres_agent_plan_repository_required")
        self.repository = repository

    @staticmethod
    def _json_list(value: Any, field: str) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)) or any(
            not isinstance(item, str) or not item for item in value
        ):
            raise RuntimeError("agent_plan_persisted_" + field + "_invalid")
        return tuple(value)

    @classmethod
    def _record(cls, bundle: dict) -> AgentPlanRecord:
        if not isinstance(bundle, dict) or not isinstance(bundle.get("plan"), dict):
            raise RuntimeError("agent_plan_persisted_aggregate_invalid")
        plan = bundle["plan"]
        required = (
            "plan_id", "tenant_id", "owner_principal_id",
            "client_idempotency_key", "request_digest", "created_at",
        )
        if any(key not in plan or plan[key] is None for key in required):
            raise RuntimeError("agent_plan_persisted_identity_invalid")
        tenant, owner = plan["tenant_id"], plan["owner_principal_id"]
        if not isinstance(tenant, str) or not tenant or not isinstance(owner, str) or not owner:
            raise RuntimeError("agent_plan_persisted_owner_invalid")
        if not isinstance(bundle.get("subtasks"), (list, tuple)):
            raise RuntimeError("agent_plan_persisted_subtasks_invalid")
        subtasks = []
        for row in bundle["subtasks"]:
            if not isinstance(row, dict):
                raise RuntimeError("agent_plan_persisted_subtask_invalid")
            try:
                approval = row["requires_approval"]
                if type(approval) is not bool:
                    raise ValueError
                subtasks.append(PlanSubtaskRecord(
                    subtask_id=row["subtask_id"],
                    agent_id=row["agent_id"],
                    action=row["action"],
                    description=row["description"],
                    required_capabilities=cls._json_list(row["capabilities_json"], "capabilities"),
                    required_permissions=cls._json_list(row["permissions_json"], "permissions"),
                    depends_on=cls._json_list(row["dependencies_json"], "dependencies"),
                    requires_approval=approval,
                    task_id=row["task_id"],
                    status=row["status"],
                ))
            except (KeyError, TypeError, ValueError) as exc:
                raise RuntimeError("agent_plan_persisted_subtask_invalid") from exc
        created_at = plan["created_at"]
        if isinstance(created_at, datetime):
            created_at = created_at.isoformat()
        if not isinstance(created_at, str) or not created_at:
            raise RuntimeError("agent_plan_persisted_created_at_invalid")
        idempotency_key = "tenant:" + __import__("hashlib").sha256(
            json.dumps([tenant, owner, plan["client_idempotency_key"]],
                       separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        return AgentPlanRecord(
            plan_id=plan["plan_id"], idempotency_key=idempotency_key,
            request_digest=plan["request_digest"], created_at=created_at,
            tenant_id=tenant, owner_principal_id=owner,
            client_idempotency_key=plan["client_idempotency_key"],
            subtasks=tuple(subtasks),
        )

    @staticmethod
    def _reservation(value: Any) -> PlanDispatchReservation | None:
        if value is None:
            return None
        expiry = value.expires_at
        return PlanDispatchReservation(
            plan_id=value.plan_id, subtask_id=value.subtask_id,
            tenant_id=value.tenant_id, owner_principal_id=value.owner_principal_id,
            generation=value.generation, claim_token=value.claim_token,
            state=value.state, task_id=value.task_id, expires_at=expiry,
        )

    @staticmethod
    def _scope(tenant_id: str | None, owner_id: str | None) -> tuple[str, str]:
        if not isinstance(tenant_id, str) or not tenant_id.strip() or not isinstance(owner_id, str) or not owner_id.strip():
            raise ValueError("distributed_agent_plan_owner_scope_required")
        return tenant_id, owner_id

    def create_plan(self, plan: AgentPlanRecord) -> AgentPlanRecord:
        if not isinstance(plan, AgentPlanRecord):
            raise TypeError("agent_plan_record_required")
        tenant, owner = self._scope(plan.tenant_id, plan.owner_principal_id)
        if not plan.client_idempotency_key:
            raise ValueError("agent_plan_client_idempotency_key_required")
        bundle = self.repository.create_plan(
            tenant_id=tenant, owner_principal_id=owner,
            client_idempotency_key=plan.client_idempotency_key,
            request_digest=plan.request_digest,
            subtasks=[{
                "subtask_id": item.subtask_id, "agent_id": item.agent_id,
                "action": item.action, "description": item.description,
                "capabilities": list(item.required_capabilities),
                "permissions": list(item.required_permissions),
                "dependencies": list(item.depends_on),
                "requires_approval": item.requires_approval,
            } for item in plan.subtasks],
        )
        # Always return the persisted canonical PostgreSQL plan ID, including replay.
        return self._record(bundle)

    def get_plan(self, *, plan_id: str, tenant_id: str | None,
                 owner_principal_id: str | None) -> AgentPlanRecord | None:
        tenant, owner = self._scope(tenant_id, owner_principal_id)
        bundle = self.repository.get_plan(
            tenant_id=tenant, owner_principal_id=owner, plan_id=plan_id,
        )
        return None if bundle is None else self._record(bundle)

    def list_subtasks(self, *, plan_id: str, tenant_id: str | None,
                      owner_principal_id: str | None) -> tuple[PlanSubtaskRecord, ...] | None:
        tenant, owner = self._scope(tenant_id, owner_principal_id)
        return self.repository.list_subtasks(
            plan_id=plan_id, tenant_id=tenant, owner_principal_id=owner,
        )

    def update_subtask(self, *, plan_id: str, subtask_id: str,
                       tenant_id: str | None, owner_principal_id: str | None,
                       status: str, task_id: str | None = None,
                       expected_statuses: Sequence[str] | None = None) -> bool:
        tenant, owner = self._scope(tenant_id, owner_principal_id)
        return self.repository.update_subtask(
            plan_id=plan_id, subtask_id=subtask_id, tenant_id=tenant,
            owner_principal_id=owner, status=status, task_id=task_id,
            expected_statuses=expected_statuses,
        )

    def reserve_dispatch(self, *, plan_id: str, subtask_id: str,
                         tenant_id: str | None, owner_principal_id: str | None
                         ) -> PlanDispatchReservation | None:
        tenant, owner = self._scope(tenant_id, owner_principal_id)
        return self._reservation(self.repository.reserve_dispatch(
            plan_id=plan_id, subtask_id=subtask_id,
            tenant_id=tenant, owner_principal_id=owner,
        ))

    def complete_dispatch(self, *, plan_id: str, subtask_id: str,
                          tenant_id: str | None, owner_principal_id: str | None,
                          claim_token: str, generation: int,
                          task_id: str, status: str) -> bool:
        tenant, owner = self._scope(tenant_id, owner_principal_id)
        return self.repository.complete_dispatch(
            plan_id=plan_id, subtask_id=subtask_id, tenant_id=tenant,
            owner_principal_id=owner, claim_token=claim_token,
            generation=generation, task_id=task_id, status=status,
        )

    def cancel_dispatch(self, *, plan_id: str, subtask_id: str,
                        tenant_id: str | None, owner_principal_id: str | None) -> bool:
        tenant, owner = self._scope(tenant_id, owner_principal_id)
        return self.repository.cancel_dispatch(
            plan_id=plan_id, subtask_id=subtask_id,
            tenant_id=tenant, owner_principal_id=owner,
        )

    def get_dispatch(self, *, plan_id: str, subtask_id: str,
                     tenant_id: str | None, owner_principal_id: str | None
                     ) -> PlanDispatchReservation | None:
        tenant, owner = self._scope(tenant_id, owner_principal_id)
        return self._reservation(self.repository.get_dispatch(
            plan_id=plan_id, subtask_id=subtask_id,
            tenant_id=tenant, owner_principal_id=owner,
        ))
