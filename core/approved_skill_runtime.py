"""Guarded runtime facade for explicitly approved, registered skills.

Discovery/registration approval and invocation approval are separate controls.
This facade never imports entrypoints and routes every invocation through the
shared guarded skill runtime.
"""
from __future__ import annotations

from typing import Any, Callable

from core.approved_skill_wiring import ApprovedSkillWiringService, SkillApprovalStore
from core.guarded_skill_runtime import OneShotSkillApprovals, SkillInvocationResult, SkillPolicyContext, invoke_registered_skill
from skills.metadata_discovery import SkillCandidate


class ApprovedSkillRuntime:
    def __init__(
        self,
        registry: Any,
        approval_store: SkillApprovalStore,
        invocation_approvals: OneShotSkillApprovals | None = None,
        audit: Callable[[str, str], None] | None = None,
        policy_engine: Any = None,
        policy_request_factory: Callable[[str, str, Any, SkillPolicyContext | None], Any] | None = None,
        approval_gateway: Any = None,
        approval_token_resolver: Callable[[Any], str | None] | None = None,
    ):
        self.registry = registry
        self.approval_store = approval_store
        self.invocation_approvals = invocation_approvals or OneShotSkillApprovals()
        if approval_gateway is None and (policy_engine is None) != (policy_request_factory is None):
            raise ValueError("policy_engine and policy_request_factory must be provided together")
        if policy_engine is not None and not callable(getattr(policy_engine, "evaluate", None)):
            raise TypeError("policy_engine must provide evaluate(request)")
        if policy_request_factory is not None and not callable(policy_request_factory):
            raise TypeError("policy_request_factory must be callable")
        if approval_gateway is not None:
            if not callable(getattr(approval_gateway, "authorize", None)):
                raise TypeError("approval_gateway must provide authorize(request, approval_token=...)")
            if not callable(getattr(getattr(approval_gateway, "policy_engine", None), "evaluate", None)):
                raise TypeError("approval_gateway must expose its policy evaluator for fail-closed token resolution")
            if policy_engine is not None:
                raise ValueError("configure either policy_engine or approval_gateway, not both")
            if not callable(policy_request_factory) or not callable(approval_token_resolver):
                raise ValueError("approval gateway requires a policy request factory and protected token resolver")
        elif approval_token_resolver is not None:
            raise ValueError("approval_token_resolver requires approval_gateway")
        self.audit = audit
        self.policy_engine = policy_engine
        self.policy_request_factory = policy_request_factory
        self.approval_gateway = approval_gateway
        self.approval_token_resolver = approval_token_resolver
        self.wiring = ApprovedSkillWiringService(registry, approval_store)

    def discover(self, root):
        return self.wiring.discover(root)

    def wire_approved(self, candidates: tuple[SkillCandidate, ...], trusted_instances: dict[str, Any]) -> tuple[str, ...]:
        # Refuse an instance whose declared risk differs from reviewed metadata.
        eligible = []
        for candidate in candidates:
            instance = trusted_instances.get(candidate.name)
            if instance is None:
                continue
            instance_risk = getattr(instance, "risk", "high")
            if not isinstance(instance_risk, str) or instance_risk.lower() != candidate.risk:
                continue
            eligible.append(candidate)
        return self.wiring.wire_approved(tuple(eligible), trusted_instances)

    def approve_invocation(self, skill_name: str, argument: str = "", *, principal_id: str | None = None) -> None:
        skill = self.registry.get(skill_name)
        if skill is None:
            raise ValueError("Cannot approve an invocation for an unregistered skill.")
        self.invocation_approvals.approve(skill_name, argument, principal_id=principal_id)

    def invoke(self, skill_name: str, argument: str = "", *, policy_context: SkillPolicyContext | None = None) -> SkillInvocationResult:
        return invoke_registered_skill(
            self.registry,
            skill_name,
            argument,
            approvals=self.invocation_approvals,
            audit=self.audit,
            policy_engine=self.policy_engine,
            policy_request_factory=self.policy_request_factory,
            policy_context=policy_context,
            approval_gateway=self.approval_gateway,
            approval_token_resolver=self.approval_token_resolver,
        )
