"""Guarded runtime facade for explicitly approved, registered skills.

Discovery/registration approval and invocation approval are separate controls.
This facade never imports entrypoints and routes every invocation through the
shared guarded skill runtime.
"""
from __future__ import annotations

from typing import Any, Callable
import hashlib
import threading

from core.approved_skill_wiring import ApprovedSkillWiringService, SkillApprovalStore
from core.guarded_skill_runtime import OneShotSkillApprovals, SkillInvocationResult, invoke_registered_skill
from skills.metadata_discovery import SkillCandidate


_MISSING_APPROVED_INSTANCE = object()


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
        self._instance_lock = threading.Lock()
        self._approved_instances: dict[tuple[str, str], Any] = {}
        self._in_flight: set[tuple[str, str]] = set()

    @staticmethod
    def _approval_key(skill_name: str, argument: str) -> tuple[str, str]:
        if not isinstance(skill_name, str) or not skill_name or len(skill_name) > 128:
            raise ValueError("A non-empty bounded skill name is required.")
        if not isinstance(argument, str) or len(argument) > 20000:
            raise ValueError("Approval argument must be text no longer than 20000 characters.")
        try:
            encoded = argument.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("Approval argument must be valid UTF-8 text.") from exc
        return skill_name, hashlib.sha256(encoded).hexdigest()

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
        key = self._approval_key(skill_name, argument)
        # Store the instance snapshot and approval together so invocation cannot
        # observe a partially-created authorization.
        with self._instance_lock:
            self.invocation_approvals.approve(skill_name, argument)
            self._approved_instances[key] = skill

    def invoke(self, skill_name: str, argument: str = "") -> SkillInvocationResult:
        skill = self.registry.get(skill_name)
        try:
            key = self._approval_key(skill_name, argument)
        except ValueError:
            key = None
        with self._instance_lock:
            if key is not None and key in self._in_flight:
                return SkillInvocationResult(
                    False, "An invocation for this approved request is already in progress.",
                    skill_name, "approval_in_use"
                )
            approved_instance = self._approved_instances.pop(key, None) if key is not None else None
            if key is not None and approved_instance is not None:
                self._in_flight.add(key)

        # Without a facade-created instance snapshot, preserve the public
        # approval_required status and never let a separately inserted approval
        # bypass the instance-binding boundary.
        if approved_instance is None and skill is not None:
            risk = str(getattr(skill, "risk", "high")).lower()
            needs_approval = (
                bool(getattr(skill, "requires_approval", True))
                or risk not in {"low", "medium", "high", "critical"}
                or risk in {"high", "critical"}
            )
            if needs_approval:
                return SkillInvocationResult(
                    False, "Skill invocation requires facade-bound approval.",
                    skill_name, "approval_required"
                )

        expected_skill = approved_instance if approved_instance is not None else skill
        try:
            return invoke_registered_skill(
                self.registry,
                skill_name,
                argument,
                approvals=self.invocation_approvals,
                audit=self.audit,
                expected_skill=expected_skill,
            )
        finally:
            if key is not None and approved_instance is not None:
                with self._instance_lock:
                    self._in_flight.discard(key)
