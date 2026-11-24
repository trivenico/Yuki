"""Validated security dependencies for production runtime composition.

The context is assembled by trusted host bootstrap code, never from prompts,
task payloads, model output, or user-controlled configuration values.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from core.authorization import Principal
from core.authorization_gateway import AuthorizationGateway
from core.authorization_audit import SQLiteAuthorizationAuditLog


@dataclass(frozen=True)
class ProductionSecurityContext:
    """One shared authorization gateway and trusted identity resolvers."""

    authorization_gateway: AuthorizationGateway
    principal_for_actor: Callable[[str], Principal]
    principal_for_worker: Callable[[str], Principal]
    policy_revision: str

    def __post_init__(self) -> None:
        if not isinstance(self.authorization_gateway, AuthorizationGateway):
            raise TypeError("production security requires an AuthorizationGateway")
        if not isinstance(self.authorization_gateway.audit_writer, SQLiteAuthorizationAuditLog):
            raise TypeError("production security requires a durable SQLiteAuthorizationAuditLog")
        for value, label in (
            (self.principal_for_actor, "principal_for_actor"),
            (self.principal_for_worker, "principal_for_worker"),
        ):
            if not callable(value):
                raise TypeError(label + " must be a trusted callable")
        if (
            not isinstance(self.policy_revision, str)
            or not self.policy_revision.strip()
            or len(self.policy_revision) > 128
        ):
            raise ValueError("policy_revision must be bounded non-empty text")

    def require_actor(self, actor_id: str) -> Principal:
        principal = self.principal_for_actor(actor_id)
        if not isinstance(principal, Principal) or principal.principal_id != actor_id:
            raise PermissionError("trusted actor identity resolution failed")
        return principal

    def require_worker(self, worker_id: str) -> Principal:
        principal = self.principal_for_worker(worker_id)
        if (
            not isinstance(principal, Principal)
            or principal.kind != "workload"
            or principal.principal_id != worker_id
            or not principal.tenant_id
            or not principal.agent_id
        ):
            raise PermissionError("trusted workload identity resolution failed")
        return principal
