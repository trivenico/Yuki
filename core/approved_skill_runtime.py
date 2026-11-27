"""Guarded runtime facade for explicitly approved, registered skills.

Discovery/registration approval and invocation approval are separate controls.
This facade never imports entrypoints and routes every invocation through the
shared guarded skill runtime. Persistent registration approval is rechecked at
dispatch so revocation blocks subsequent invocations of wired skills.
"""
from __future__ import annotations

from typing import Any, Callable

from core.approved_skill_wiring import ApprovedSkillWiringService, SkillApprovalStore
from core.guarded_skill_runtime import (
    OneShotSkillApprovals,
    SkillInvocationResult,
    invoke_registered_skill,
)
from skills.metadata_discovery import SkillCandidate


class ApprovedSkillRuntime:
    def __init__(
        self,
        registry: Any,
        approval_store: SkillApprovalStore,
        invocation_approvals: OneShotSkillApprovals | None = None,
        audit: Callable[[str, str], None] | None = None,
    ):
        self.registry = registry
        self.approval_store = approval_store
        self.invocation_approvals = invocation_approvals or OneShotSkillApprovals()
        self.audit = audit
        self.wiring = ApprovedSkillWiringService(registry, approval_store)
        # Retain the exact reviewed candidate and instance used for each skill
        # wired through this facade. This prevents a registry replacement from
        # inheriting the old candidate's persistent approval.
        self._wired_skills: dict[str, tuple[SkillCandidate, Any]] = {}

    def discover(self, root):
        return self.wiring.discover(root)

    def wire_approved(self, candidates: tuple[SkillCandidate, ...], trusted_instances: dict[str, Any]) -> tuple[str, ...]:
        # Refuse an instance whose declared risk differs from reviewed metadata.
        eligible = []
        instances = {}
        for candidate in candidates:
            instance = trusted_instances.get(candidate.name)
            if instance is None:
                continue
            instance_risk = getattr(instance, "risk", "high")
            if not isinstance(instance_risk, str) or instance_risk.lower() != candidate.risk:
                continue
            # Persistent registration approval and per-invocation approval are
            # distinct. Candidate metadata and skill class defaults cannot
            # silently disable the latter for dynamically wired code.
            try:
                instance.requires_approval = True
            except Exception:
                continue
            if getattr(instance, "requires_approval", None) is not True:
                continue
            eligible.append(candidate)
            instances[candidate.name] = instance
        wired = self.wiring.wire_approved(tuple(eligible), trusted_instances)
        for name in wired:
            candidate = next(item for item in eligible if item.name == name)
            self._wired_skills[name] = (candidate, instances[name])
        return wired

    def approve_invocation(self, skill_name: str, argument: str = "") -> None:
        skill = self.registry.get(skill_name)
        if skill is None:
            raise ValueError("Cannot approve an invocation for an unregistered skill.")
        self.invocation_approvals.approve(skill_name, argument)

    def invoke(self, skill_name: str, argument: str = "") -> SkillInvocationResult:
        wired = self._wired_skills.get(skill_name)
        if wired is not None:
            candidate, expected_instance = wired
            try:
                approved = self.approval_store.is_approved(candidate)
            except Exception:
                approved = False
            if not approved:
                if self.audit is not None:
                    try:
                        self.audit("skill:invoke", "approval_revoked")
                    except Exception:
                        pass
                return SkillInvocationResult(
                    False,
                    "Skill approval is missing or revoked; invocation was blocked.",
                    skill_name,
                    "approval_revoked",
                )
            return invoke_registered_skill(
                self.registry,
                skill_name,
                argument,
                approvals=self.invocation_approvals,
                audit=self.audit,
                expected_skill=expected_instance,
            )
        return invoke_registered_skill(
            self.registry,
            skill_name,
            argument,
            approvals=self.invocation_approvals,
            audit=self.audit,
        )
