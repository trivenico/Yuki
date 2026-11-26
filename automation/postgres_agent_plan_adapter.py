"""Typed adapter from the coordinator plan port to PostgreSQL persistence.

The raw PostgreSQL repository intentionally exposes its outbox worker API
separately. This adapter translates only the coordinator aggregate contract.
"""
from __future__ import annotations

import json
from datetime import datetime
from dataclasses import dataclass
from typing import Any, Mapping, Sequence
from types import MappingProxyType

from automation.agent_plan_repository import (
    AgentPlanRecord,
    DispatchReservation as PlanDispatchReservation,
    PlanSubtaskRecord,
)
from automation.postgres_agent_plan_repository import PostgresAgentPlanRepository


@dataclass(frozen=True)
class ClaimedAgentPlanDispatch:
    """Verified immutable worker input plus both PostgreSQL fencing credentials."""

    tenant_id: str
    owner_principal_id: str
    plan_id: str
    subtask_id: str
    reservation_token: str
    dispatch_generation: int
    claim_token: str
    claim_generation: int
    envelope_version: int
    envelope_digest: str
    envelope: Mapping[str, Any]


def _freeze_json(value: Any) -> Any:
    """Recursively freeze JSON data so a frozen claim cannot be mutated."""
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


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


    def attach_dispatch_envelope(self, **kwargs) -> str:
        """Forward the immutable envelope write to the PostgreSQL authority."""
        return self.repository.attach_dispatch_envelope(**kwargs)

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

    # Worker-only operations are deliberately separate from the coordinator port.
    def claim_outbox(self, *, limit: int = 20) -> list[ClaimedAgentPlanDispatch]:
        rows = self.repository.claim_outbox(limit=limit)
        claims = []
        required = {
            "version", "tenant_id", "owner_principal_id", "plan_id", "subtask_id",
            "reservation_generation", "idempotency_key", "agent_id", "action",
            "manifest_revision", "required_capabilities", "required_permissions",
            "requires_approval", "dependency_snapshot", "dependency_output_digests",
            "argument",
        }
        for row in rows:
            if not isinstance(row, dict):
                raise RuntimeError("agent_plan_outbox_claim_invalid")
            envelope = row.get("envelope_json")
            if not isinstance(envelope, dict) or set(envelope) != required:
                raise RuntimeError("agent_plan_outbox_envelope_schema_invalid")
            identity = (
                row.get("tenant_id"), row.get("owner_principal_id"),
                row.get("plan_id"), row.get("subtask_id"),
                row.get("dispatch_generation"),
            )
            envelope_identity = (
                envelope.get("tenant_id"), envelope.get("owner_principal_id"),
                envelope.get("plan_id"), envelope.get("subtask_id"),
                envelope.get("reservation_generation"),
            )
            if identity != envelope_identity:
                raise RuntimeError("agent_plan_outbox_envelope_scope_mismatch")
            if (row.get("envelope_version") != 1 or envelope.get("version") != 1
                    or type(row.get("dispatch_generation")) is not int
                    or type(row.get("claim_generation")) is not int
                    or row["claim_generation"] < 1
                    or not isinstance(row.get("reservation_token"), str)
                    or len(row["reservation_token"]) < 32
                    or not isinstance(row.get("claim_token"), str)
                    or len(row["claim_token"]) < 32
                    or not isinstance(row.get("envelope_digest"), str)
                    or len(row["envelope_digest"]) != 64):
                raise RuntimeError("agent_plan_outbox_claim_fence_invalid")
            claims.append(ClaimedAgentPlanDispatch(
                tenant_id=row["tenant_id"],
                owner_principal_id=row["owner_principal_id"],
                plan_id=row["plan_id"],
                subtask_id=row["subtask_id"],
                reservation_token=row["reservation_token"],
                dispatch_generation=row["dispatch_generation"],
                claim_token=row["claim_token"],
                claim_generation=row["claim_generation"],
                envelope_version=1,
                envelope_digest=row["envelope_digest"],
                envelope=_freeze_json(envelope),
            ))
        return claims

    def recover_stale_outbox(
        self, *, stale_after_seconds: int = 60, max_attempts: int = 8,
        limit: int = 100,
    ) -> dict[str, int]:
        """Recover expired processing claims through the repository's bounded backoff policy."""
        result = self.repository.recover_stale_outbox(
            stale_after_seconds=stale_after_seconds,
            max_attempts=max_attempts,
            limit=limit,
        )
        if (
            not isinstance(result, dict)
            or type(result.get("requeued")) is not int
            or type(result.get("dead_lettered")) is not int
            or result["requeued"] < 0
            or result["dead_lettered"] < 0
        ):
            raise RuntimeError("agent_plan_outbox_recovery_result_invalid")
        return {"requeued": result["requeued"], "dead_lettered": result["dead_lettered"]}

    def dead_letter_claimed_dispatch(
        self, *, tenant_id: str, owner_principal_id: str, plan_id: str,
        subtask_id: str, reservation_token: str, dispatch_generation: int,
        outbox_claim_token: str, outbox_claim_generation: int,
        failure_code: str,
    ) -> bool:
        tenant, owner = self._scope(tenant_id, owner_principal_id)
        return self.repository.dead_letter_claimed_dispatch(
            tenant_id=tenant, owner_principal_id=owner, plan_id=plan_id,
            subtask_id=subtask_id, reservation_token=reservation_token,
            dispatch_generation=dispatch_generation,
            outbox_claim_token=outbox_claim_token,
            outbox_claim_generation=outbox_claim_generation,
            failure_code=failure_code,
        )

    def heartbeat_outbox_claim(
        self, *, tenant_id: str, owner_principal_id: str, plan_id: str,
        subtask_id: str, reservation_token: str, generation: int,
        claim_token: str, claim_generation: int, ttl_seconds: int = 60,
    ) -> bool:
        tenant, owner = self._scope(tenant_id, owner_principal_id)
        return self.repository.heartbeat_outbox_claim(
            tenant_id=tenant, owner_principal_id=owner, plan_id=plan_id,
            subtask_id=subtask_id, reservation_token=reservation_token,
            generation=generation, claim_token=claim_token,
            claim_generation=claim_generation, ttl_seconds=ttl_seconds,
        )

    def complete_claimed_dispatch(
        self, *, tenant_id: str, owner_principal_id: str, plan_id: str,
        subtask_id: str, reservation_token: str, dispatch_generation: int,
        outbox_claim_token: str, outbox_claim_generation: int,
        task_id: str, status: str,
    ) -> bool:
        tenant, owner = self._scope(tenant_id, owner_principal_id)
        return self.repository.complete_claimed_dispatch(
            tenant_id=tenant, owner_principal_id=owner, plan_id=plan_id,
            subtask_id=subtask_id, reservation_token=reservation_token,
            dispatch_generation=dispatch_generation,
            outbox_claim_token=outbox_claim_token,
            outbox_claim_generation=outbox_claim_generation,
            task_id=task_id, status=status,
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
