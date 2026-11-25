"""Finite-cycle, fail-closed PostgreSQL specialist outbox dispatcher.

The dispatcher only creates held tasks. The existing dual-fenced repository
transaction is the sole operation that makes a task visible to ordinary workers.
"""
from __future__ import annotations

import hashlib
from typing import Any, Callable, Mapping

from core.authorization import Principal
from core.authorization_gateway import AuthorizationGateway

from automation.postgres_agent_plan_adapter import (
    ClaimedAgentPlanDispatch,
    PostgresAgentPlanRepositoryAdapter,
)
from automation.specialist_dispatch_security import (
    authorize_specialist_dispatch,
    resolve_human_dispatch_owner,
    resolve_specialist_workload_principal,
)
from automation.task_runtime import TaskRuntime


class PermanentDispatchError(RuntimeError):
    """A bounded, non-sensitive reason that makes this claim non-retryable."""

    def __init__(self, failure_code: str):
        if (
            not isinstance(failure_code, str)
            or not failure_code
            or len(failure_code) > 80
            or not failure_code.isascii()
            or not failure_code.replace("_", "").isalnum()
            or not failure_code[0].isalpha()
            or failure_code.lower() != failure_code
        ):
            raise ValueError("dispatch_failure_code_invalid")
        self.failure_code = failure_code
        super().__init__(failure_code)


class PostgresAgentPlanOutboxDispatcher:
    """Process a bounded batch of immutable claims without owning a background loop.

    Identity resolvers and the audit writer are deployment-owned dependencies.
    Audit events contain identifiers and classifications only, never arguments,
    provider output, credentials, exception strings, or envelope contents.
    """

    def __init__(
        self,
        *,
        repository: PostgresAgentPlanRepositoryAdapter,
        runtime: TaskRuntime,
        agent_registry: Any,
        authorization_gateway: AuthorizationGateway,
        policy_revision: str,
        resolve_human_principal: Callable[[str], Principal],
        resolve_workload_principal: Callable[[str], Principal],
        audit_writer: Callable[..., None],
        batch_size: int = 10,
        lease_ttl_seconds: int = 60,
    ) -> None:
        if not isinstance(repository, PostgresAgentPlanRepositoryAdapter):
            raise TypeError("typed_postgres_agent_plan_adapter_required")
        if not isinstance(runtime, TaskRuntime):
            raise TypeError("task_runtime_required")
        if not isinstance(authorization_gateway, AuthorizationGateway):
            raise TypeError("central_authorization_gateway_required")
        if not isinstance(policy_revision, str) or not policy_revision.strip() or len(policy_revision) > 128:
            raise ValueError("dispatch_policy_revision_invalid")
        if not callable(resolve_human_principal) or not callable(resolve_workload_principal):
            raise TypeError("trusted_dispatch_identity_resolvers_required")
        if not callable(audit_writer):
            raise TypeError("durable_redacted_dispatch_audit_writer_required")
        if type(batch_size) is not int or not 1 <= batch_size <= 100:
            raise ValueError("dispatch_batch_size_invalid")
        if type(lease_ttl_seconds) is not int or not 5 <= lease_ttl_seconds <= 900:
            raise ValueError("dispatch_lease_ttl_invalid")
        if getattr(runtime.store, "database_identity", None) != getattr(
            repository.repository, "database_identity", None
        ):
            raise ValueError("dispatch_repositories_must_share_postgres_database")
        if getattr(agent_registry, "database_identity", None) != getattr(
            runtime.store, "database_identity", None
        ):
            raise ValueError("dispatch_registry_must_share_postgres_database")
        if getattr(runtime, "authorization_gateway", None) is not authorization_gateway:
            raise ValueError("dispatch_gateway_must_match_task_runtime_gateway")
        self.repository = repository
        self.runtime = runtime
        self.agent_registry = agent_registry
        self.authorization_gateway = authorization_gateway
        self.policy_revision = policy_revision
        self.resolve_human_principal = resolve_human_principal
        self.resolve_workload_principal = resolve_workload_principal
        self.audit_writer = audit_writer
        self.batch_size = batch_size
        self.lease_ttl_seconds = lease_ttl_seconds

    def _audit(self, event_type: str, claim: ClaimedAgentPlanDispatch, **metadata: Any) -> None:
        # Explicit allow-list prevents callers from accidentally writing sensitive payloads.
        allowed = {"task_id", "failure_code", "manifest_revision"}
        if set(metadata) - allowed:
            raise ValueError("dispatch_audit_metadata_not_allowed")
        self.audit_writer(
            event_type=event_type,
            tenant_id=claim.tenant_id,
            owner_principal_id=claim.owner_principal_id,
            plan_id=claim.plan_id,
            subtask_id=claim.subtask_id,
            dispatch_generation=claim.dispatch_generation,
            claim_generation=claim.claim_generation,
            metadata=dict(metadata),
        )

    def _heartbeat(self, claim: ClaimedAgentPlanDispatch) -> None:
        alive = self.repository.heartbeat_outbox_claim(
            tenant_id=claim.tenant_id,
            owner_principal_id=claim.owner_principal_id,
            plan_id=claim.plan_id,
            subtask_id=claim.subtask_id,
            reservation_token=claim.reservation_token,
            generation=claim.dispatch_generation,
            claim_token=claim.claim_token,
            claim_generation=claim.claim_generation,
            ttl_seconds=self.lease_ttl_seconds,
        )
        if alive is not True:
            raise RuntimeError("dispatch_claim_fence_lost")

    def _validate_envelope(self, claim: ClaimedAgentPlanDispatch) -> Mapping[str, Any]:
        envelope = claim.envelope
        required = {
            "version", "tenant_id", "owner_principal_id", "plan_id", "subtask_id",
            "reservation_generation", "idempotency_key", "agent_id", "action",
            "manifest_revision", "required_capabilities", "required_permissions",
            "requires_approval", "dependency_snapshot", "dependency_output_digests",
            "argument",
        }
        if not isinstance(envelope, Mapping) or set(envelope) != required:
            raise PermanentDispatchError("envelope_schema_invalid")
        expected = (
            claim.tenant_id, claim.owner_principal_id, claim.plan_id,
            claim.subtask_id, claim.dispatch_generation,
        )
        actual = (
            envelope["tenant_id"], envelope["owner_principal_id"],
            envelope["plan_id"], envelope["subtask_id"],
            envelope["reservation_generation"],
        )
        if actual != expected or envelope["version"] != 1:
            raise PermanentDispatchError("envelope_scope_mismatch")
        key = envelope["idempotency_key"]
        agent_id, action = envelope["agent_id"], envelope["action"]
        argument = envelope["argument"]
        if (
            not isinstance(key, str) or not key or len(key) > 200 or "\x00" in key
            or not isinstance(agent_id, str) or not agent_id or len(agent_id) > 80
            or not isinstance(action, str) or action != "emit"
            or not isinstance(argument, str) or not argument.strip()
            or len(argument.encode("utf-8")) > 16_384 or "\x00" in argument
            or type(envelope["requires_approval"]) is not bool
            or type(envelope["manifest_revision"]) is not int
            or envelope["manifest_revision"] < 1
        ):
            raise PermanentDispatchError("envelope_fields_invalid")
        for field in ("required_capabilities", "required_permissions"):
            values = envelope[field]
            if (
                not isinstance(values, tuple)
                or len(values) > 32
                or any(not isinstance(value, str) or not value or len(value) > 100 for value in values)
                or len(set(values)) != len(values)
            ):
                raise PermanentDispatchError("envelope_requirements_invalid")
        if not isinstance(envelope["dependency_snapshot"], tuple) or len(envelope["dependency_snapshot"]) > 20:
            raise PermanentDispatchError("dependency_snapshot_invalid")
        if not isinstance(envelope["dependency_output_digests"], Mapping):
            raise PermanentDispatchError("dependency_digests_invalid")
        return envelope

    def _verify_dependencies(
        self, claim: ClaimedAgentPlanDispatch, envelope: Mapping[str, Any], owner: Principal
    ) -> None:
        snapshot = envelope["dependency_snapshot"]
        digests = envelope["dependency_output_digests"]
        seen = set()
        for item in snapshot:
            if not isinstance(item, Mapping) or set(item) != {"subtask_id", "task_id", "state"}:
                raise PermanentDispatchError("dependency_snapshot_invalid")
            subtask_id, task_id, state = item["subtask_id"], item["task_id"], item["state"]
            if (
                not isinstance(subtask_id, str) or not subtask_id or subtask_id in seen
                or not isinstance(task_id, str) or not task_id
                or state != "succeeded"
            ):
                raise PermanentDispatchError("dependency_not_succeeded")
            seen.add(subtask_id)
            try:
                task = self.runtime.get(actor_id=owner.principal_id, task_id=task_id)
            except (KeyError, PermissionError) as exc:
                raise PermanentDispatchError("dependency_unavailable") from exc
            if task.state != "succeeded":
                raise PermanentDispatchError("dependency_not_succeeded")
            if subtask_id in digests:
                result = task.result
                output = result.get("output") if isinstance(result, Mapping) else None
                if not isinstance(output, str):
                    raise PermanentDispatchError("dependency_output_missing")
                digest = hashlib.sha256(output.encode("utf-8")).hexdigest()
                if digests[subtask_id] != digest:
                    raise PermanentDispatchError("dependency_output_changed")
        if set(digests) - seen:
            raise PermanentDispatchError("dependency_digest_orphaned")

    def _verify_manifest(self, envelope: Mapping[str, Any]) -> None:
        agent_id = envelope["agent_id"]
        try:
            revision = self.agent_registry.get_manifest_revision(agent_id)
            agents = self.agent_registry.list_agents()
        except Exception as exc:
            raise RuntimeError("dispatch_registry_unavailable") from exc
        if type(revision) is not int or revision != envelope["manifest_revision"]:
            raise PermanentDispatchError("manifest_revision_changed")
        matches = [agent for agent in agents if getattr(agent, "agent_id", None) == agent_id]
        if len(matches) != 1:
            raise PermanentDispatchError("manifest_missing")
        agent = matches[0]
        if (
            getattr(agent, "enabled", False) is not True
            or getattr(agent, "action", None) != envelope["action"]
            or not set(envelope["required_capabilities"]) <= set(agent.capabilities)
            or not set(envelope["required_permissions"]) <= set(agent.permissions)
        ):
            raise PermanentDispatchError("manifest_policy_changed")

    def _process(self, claim: ClaimedAgentPlanDispatch) -> str:
        self._heartbeat(claim)
        envelope = self._validate_envelope(claim)
        try:
            owner = resolve_human_dispatch_owner(
                self.resolve_human_principal,
                tenant_id=claim.tenant_id,
                owner_principal_id=claim.owner_principal_id,
            )
            workload = resolve_specialist_workload_principal(
                self.resolve_workload_principal,
                tenant_id=claim.tenant_id,
                agent_id=envelope["agent_id"],
            )
            if (
                workload.agent_id != envelope["agent_id"]
                or not set(envelope["required_permissions"]) <= set(workload.permissions)
            ):
                raise PermanentDispatchError("workload_identity_mismatch")
            authorize_specialist_dispatch(
                self.authorization_gateway,
                owner=owner,
                tenant_id=claim.tenant_id,
                plan_id=claim.plan_id,
                policy_revision=self.policy_revision,
            )
        except PermissionError as exc:
            raise PermanentDispatchError("dispatch_identity_or_authorization_denied") from exc

        self._verify_manifest(envelope)
        self._verify_dependencies(claim, envelope, owner)
        self._heartbeat(claim)

        # A retry after process death must recover the exact held task, never
        # accept a visible, running, terminal, or foreign-owned task.
        task = self.runtime.get_by_idempotency_key(
            actor_id=owner.principal_id,
            idempotency_key=envelope["idempotency_key"],
        )
        if task is None:
            self._audit("agent_dispatch.authorized", claim, manifest_revision=envelope["manifest_revision"])
            task = self.runtime.enqueue(
                actor_id=owner.principal_id,
                idempotency_key=envelope["idempotency_key"],
                name=f"agent-{envelope['agent_id']}-{claim.subtask_id}",
                steps=[{
                    "name": claim.subtask_id,
                    "action": "emit",
                    "argument": envelope["argument"],
                    "requires_approval": envelope["requires_approval"],
                }],
                dispatch_ready=False,
            )
        if (
            task.state != "queued"
            or task.dispatch_ready is not False
            or len(task.steps) != 1
            or task.steps[0].get("action") != "emit"
            or task.steps[0].get("argument") != envelope["argument"]
            or task.steps[0].get("requires_approval") is not envelope["requires_approval"]
        ):
            raise PermanentDispatchError("recovered_task_mismatch")

        self._heartbeat(claim)
        self._audit("agent_dispatch.task_held", claim, task_id=task.task_id)
        completed = self.repository.complete_claimed_dispatch(
            tenant_id=claim.tenant_id,
            owner_principal_id=claim.owner_principal_id,
            plan_id=claim.plan_id,
            subtask_id=claim.subtask_id,
            reservation_token=claim.reservation_token,
            dispatch_generation=claim.dispatch_generation,
            outbox_claim_token=claim.claim_token,
            outbox_claim_generation=claim.claim_generation,
            task_id=task.task_id,
            status="queued",
        )
        if completed is not True:
            raise RuntimeError("dispatch_completion_fence_lost")
        return task.task_id

    def run_cycle(self) -> dict[str, int]:
        """Recover stale claims, then process at most batch_size immutable claims."""
        recovery = self.repository.recover_stale_outbox(limit=self.batch_size * 2)
        claims = self.repository.claim_outbox(limit=self.batch_size)
        delivered = dead_lettered = deferred = 0
        for claim in claims:
            try:
                self._process(claim)
                delivered += 1
            except PermanentDispatchError as exc:
                # The durable audit write must succeed before the fenced terminal
                # transition; audit outages leave the claim recoverable by lease.
                self._audit("agent_dispatch.dead_letter", claim, failure_code=exc.failure_code)
                if self.repository.dead_letter_claimed_dispatch(
                    tenant_id=claim.tenant_id,
                    owner_principal_id=claim.owner_principal_id,
                    plan_id=claim.plan_id,
                    subtask_id=claim.subtask_id,
                    reservation_token=claim.reservation_token,
                    dispatch_generation=claim.dispatch_generation,
                    outbox_claim_token=claim.claim_token,
                    outbox_claim_generation=claim.claim_generation,
                    failure_code=exc.failure_code,
                ):
                    dead_lettered += 1
                else:
                    deferred += 1
            except Exception:
                # Do not persist exception text or make an unfenced state change.
                # The repository's bounded stale-claim recovery applies backoff.
                deferred += 1
        return {
            "recovered_requeued": recovery["requeued"],
            "recovered_dead_lettered": recovery["dead_lettered"],
            "claimed": len(claims),
            "delivered": delivered,
            "dead_lettered": dead_lettered,
            "deferred": deferred,
        }
