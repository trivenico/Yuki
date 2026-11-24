"""Validated security dependencies for production runtime composition.

This object is assembled by trusted host bootstrap code. It is not populated
from configuration files, task payloads, prompts, or model-generated arguments.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from core.authorization import Principal
from core.authorization_gateway import AuthorizationGateway
from core.delegation_authority import DelegationAuthority
from core.workload_identity import WorkloadCredentialVerifier


@dataclass(frozen=True)
class ProductionSecurityContext:
    """One shared, fail-closed security dependency set for production workers."""

    authorization_gateway: AuthorizationGateway
    principal_for_actor: Callable[[str], Principal]
    principal_for_worker: Callable[[str], Principal]
    policy_revision: str
    workload_credential_verifier: WorkloadCredentialVerifier
    workload_audience: str
    delegation_authority: DelegationAuthority
    workload_credential_provider: Callable[[str, str, str], str]

    def __post_init__(self) -> None:
        if not isinstance(self.authorization_gateway, AuthorizationGateway):
            raise TypeError("production security requires an AuthorizationGateway")
        for value, label in (
            (self.principal_for_actor, "principal_for_actor"),
            (self.principal_for_worker, "principal_for_worker"),
            (self.workload_credential_provider, "workload_credential_provider"),
        ):
            if not callable(value):
                raise TypeError(label + " must be a trusted callable")
        if not isinstance(self.policy_revision, str) or not self.policy_revision.strip() or len(self.policy_revision) > 128:
            raise ValueError("policy_revision must be bounded non-empty text")
        if not callable(getattr(self.workload_credential_verifier, "verify", None)):
            raise TypeError("workload_credential_verifier must implement verify()")
        if not isinstance(self.workload_audience, str) or not self.workload_audience.strip() or len(self.workload_audience) > 256:
            raise ValueError("workload_audience must be bounded non-empty text")
        if not isinstance(self.delegation_authority, DelegationAuthority):
            raise TypeError("production security requires a DelegationAuthority")

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
