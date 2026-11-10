"""Guarded runtime facade for explicitly approved, registered skills.

Discovery/registration approval and invocation approval are separate controls.
This facade never imports entrypoints and routes every invocation through the
shared guarded skill runtime.
"""
from __future__ import annotations

from typing import Any, Callable

from core.approved_skill_wiring import ApprovedSkillWiringService, SkillApprovalStore
from core.guarded_skill_runtime import OneShotSkillApprovals, SkillInvocationResult, invoke_registered_skill
from skills.metadata_discovery import SkillCandidate


class ApprovedSkillRuntime:
    def __init__(
        self,
        registry: Any,
        approval_store: SkillApprovalStore,
        invocation_approvals: OneShotSkillApprovals | None = None,
        audit: Callable[[str, str], None] | None = None,
        policy_engine: Any = None,
        policy_request_factory: Callable[[str, str, Any], Any] | None = None,
    ):
        self.registry = registry
        self.approval_store = approval_store
        self.invocation_approvals = invocation_approvals or OneShotSkillApprovals()
        if (policy_engine is None) != (policy_request_factory is None):
            raise ValueError("policy_engine and policy_request_factory must be provided together")
        if policy_engine is not None and not callable(getattr(policy_engine, "evaluate", None)):
            raise TypeError("policy_engine must provide evaluate(request)")
        if policy_request_factory is not None and not callable(policy_request_factory):
            raise TypeError("policy_request_factory must be callable")
        self.audit = audit
        self.policy_engine = policy_engine
        self.policy_request_factory = policy_request_factory
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

    def approve_invocation(self, skill_name: str, argument: str = "") -> None:
        skill = self.registry.get(skill_name)
        if skill is None:
            raise ValueError("Cannot approve an invocation for an unregistered skill.")
        self.invocation_approvals.approve(skill_name, argument)

    def invoke(self, skill_name: str, argument: str = "") -> SkillInvocationResult:
        return invoke_registered_skill(
            self.registry,
            skill_name,
            argument,
            approvals=self.invocation_approvals,
            audit=self.audit,
            policy_engine=self.policy_engine,
            policy_request_factory=self.policy_request_factory,
        )
