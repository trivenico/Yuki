"""Trusted identity and policy gates for specialist outbox delivery.

These helpers deliberately do not dispatch tasks. They centralize the checks
that a future supervised dispatcher must perform before any enqueue side effect.
"""
from __future__ import annotations

from typing import Callable

from core.authorization import AuthorizationRequest, Principal
from core.authorization_gateway import AuthorizationGateway


def _scope_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 256 or "\x00" in value:
        raise ValueError("specialist_dispatch_" + field + "_invalid")
    return value


def resolve_human_dispatch_owner(
    resolver: Callable[[str], Principal],
    *,
    tenant_id: str,
    owner_principal_id: str,
) -> Principal:
    """Resolve an owner from trusted identity state; never build it from an envelope."""
    if not callable(resolver):
        raise TypeError("trusted_owner_principal_resolver_required")
    tenant = _scope_text(tenant_id, "tenant")
    owner = _scope_text(owner_principal_id, "owner")
    try:
        principal = resolver(owner)
    except Exception as exc:
        raise PermissionError("specialist_dispatch_owner_resolution_failed") from exc
    if (
        not isinstance(principal, Principal)
        or principal.kind != "human"
        or principal.principal_id != owner
        or principal.tenant_id != tenant
        or principal.agent_id is not None
    ):
        raise PermissionError("specialist_dispatch_owner_scope_mismatch")
    return principal


def resolve_specialist_workload_principal(
    resolver: Callable[[str], Principal],
    *,
    tenant_id: str,
    agent_id: str,
) -> Principal:
    """Resolve the specialist workload identity from the trusted registry boundary."""
    if not callable(resolver):
        raise TypeError("trusted_specialist_workload_resolver_required")
    tenant = _scope_text(tenant_id, "tenant")
    agent = _scope_text(agent_id, "agent")
    try:
        principal = resolver(agent)
    except Exception as exc:
        raise PermissionError("specialist_dispatch_workload_resolution_failed") from exc
    if (
        not isinstance(principal, Principal)
        or principal.kind != "workload"
        or principal.agent_id != agent
        or principal.tenant_id != tenant
    ):
        raise PermissionError("specialist_dispatch_workload_scope_mismatch")
    return principal


def authorize_specialist_dispatch(
    gateway: AuthorizationGateway,
    *,
    owner: Principal,
    tenant_id: str,
    plan_id: str,
    policy_revision: str,
):
    """Require a fresh, audited allow for this exact owner and plan resource."""
    if not isinstance(gateway, AuthorizationGateway):
        raise TypeError("central_authorization_gateway_required")
    if not isinstance(owner, Principal) or owner.kind != "human" or owner.agent_id is not None:
        raise TypeError("verified_human_dispatch_owner_required")
    tenant = _scope_text(tenant_id, "tenant")
    if owner.tenant_id != tenant:
        raise PermissionError("specialist_dispatch_owner_scope_mismatch")
    plan = _scope_text(plan_id, "plan")
    revision = _scope_text(policy_revision, "policy_revision")
    request = AuthorizationRequest(
        principal=owner,
        action="agent:dispatch",
        resource="agent-plan:" + plan,
        risk="high",
        expected_policy_revision=revision,
    )
    decision = gateway.authorize(request)
    if decision.allowed is not True or decision.policy_revision != revision:
        raise PermissionError("specialist_dispatch_authorization_denied")
    return decision
