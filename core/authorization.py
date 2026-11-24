"""Typed, fail-closed authorization contracts for Yuki's control plane.

This module defines policy data structures only. Callers must still enforce the
returned decision at the resource-owning boundary; a decision is not a bearer token.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

_MAX_ID = 256
_MAX_ACTION = 128
_MAX_REVISION = 128
_RISKS = frozenset({"low", "medium", "high", "critical"})
_PRINCIPAL_KINDS = frozenset({"human", "workload", "service"})


def _required_text(value: object, label: str, maximum: int = _MAX_ID) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum or value != value.strip():
        raise ValueError(f"{label} must be a non-empty, trimmed string of at most {maximum} characters")
    return value


def _optional_text(value: object, label: str, maximum: int = _MAX_ID) -> None:
    if value is not None:
        _required_text(value, label, maximum)


@dataclass(frozen=True)
class Principal:
    """Authenticated actor identity resolved by trusted application code."""

    principal_id: str
    kind: str
    tenant_id: str | None = None
    agent_id: str | None = None
    permissions: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        _required_text(self.principal_id, "principal_id")
        if self.kind not in _PRINCIPAL_KINDS:
            raise ValueError("principal kind is invalid")
        _optional_text(self.tenant_id, "tenant_id")
        _optional_text(self.agent_id, "agent_id")
        if not isinstance(self.permissions, frozenset) or len(self.permissions) > 256:
            raise ValueError("permissions must be a bounded frozenset")
        for permission in self.permissions:
            _required_text(permission, "permission", _MAX_ACTION)


@dataclass(frozen=True)
class AuthorizationRequest:
    """Immutable description of one attempted action at an enforcement point."""

    principal: Principal
    action: str
    resource: str
    risk: str = "medium"
    task_id: str | None = None
    delegation_id: str | None = None
    approval_id: str | None = None
    expected_policy_revision: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.principal, Principal):
            raise TypeError("principal must be a trusted Principal instance")
        _required_text(self.action, "action", _MAX_ACTION)
        _required_text(self.resource, "resource")
        if self.risk not in _RISKS:
            raise ValueError("risk must be low, medium, high, or critical")
        for field_name in ("task_id", "delegation_id", "approval_id", "expected_policy_revision"):
            _optional_text(getattr(self, field_name), field_name, _MAX_REVISION)


@dataclass(frozen=True)
class AuthorizationDecision:
    """A policy result; it is deliberately not transferable authorization."""

    allowed: bool
    decision_id: str
    policy_revision: str
    reason_code: str

    def __post_init__(self) -> None:
        if type(self.allowed) is not bool:
            raise TypeError("allowed must be a literal boolean")
        _required_text(self.decision_id, "decision_id")
        _required_text(self.policy_revision, "policy_revision", _MAX_REVISION)
        _required_text(self.reason_code, "reason_code", _MAX_ACTION)


@runtime_checkable
class AuthorizationPolicy(Protocol):
    def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        """Return a typed decision for the complete immutable request."""


def evaluate_authorization(
    policy: AuthorizationPolicy,
    request: AuthorizationRequest,
) -> AuthorizationDecision:
    """Evaluate policy and fail closed on exceptions, malformed results or stale policy."""
    if not isinstance(request, AuthorizationRequest):
        raise TypeError("request must be an AuthorizationRequest")
    try:
        if not isinstance(policy, AuthorizationPolicy):
            raise TypeError("policy contract is invalid")
        decision = policy.authorize(request)
        if not isinstance(decision, AuthorizationDecision):
            raise TypeError("policy returned an invalid decision")
        if request.expected_policy_revision is not None and (
            decision.policy_revision != request.expected_policy_revision
        ):
            return AuthorizationDecision(
                False, "local-policy-revision-mismatch",
                request.expected_policy_revision, "stale_policy",
            )
        return decision
    except Exception:
        return AuthorizationDecision(
            False, "local-policy-evaluation-failed", "unavailable", "policy_unavailable",
        )


def require_authorized(
    policy: AuthorizationPolicy,
    request: AuthorizationRequest,
) -> AuthorizationDecision:
    """Raise PermissionError unless policy returns a valid, current allow decision."""
    decision = evaluate_authorization(policy, request)
    if decision.allowed is not True:
        raise PermissionError("authorization_denied:" + decision.reason_code)
    return decision
