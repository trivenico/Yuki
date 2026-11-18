"""Adapter from supervised workforce jobs to Yuki's approval-gated agent.

Only skills that explicitly declare capability and permission metadata can be
selected. The adapter delegates invocation to YukiCoreAgent.run_skill(), which
performs a fresh PermissionGate request and the shared guarded skill invocation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WorkforceExecutionOutcome:
    ok: bool
    status: str
    message: str


class YukiAgentWorkforceExecutor:
    """Execute one workforce job through a configured YukiCoreAgent."""

    def __init__(self, agent: Any):
        if not callable(getattr(agent, "run_skill", None)):
            raise TypeError("agent must provide run_skill()")
        registry = getattr(agent, "registry", None)
        if not callable(getattr(registry, "get", None)) or not callable(getattr(registry, "names", None)):
            raise TypeError("agent registry must provide get() and names()")
        self.agent = agent
        self._registry = registry
        self._skill_snapshot = self._snapshot_registry(registry)

    @classmethod
    def _snapshot_registry(cls, registry):
        snapshot = {}
        for name in registry.names():
            skill = registry.get(name)
            if skill is None or getattr(skill, "name", None) != name:
                raise ValueError("Skill registry returned an inconsistent entry.")
            capabilities = cls._declared_set(skill, "capabilities")
            permissions = cls._declared_set(skill, "permissions")
            risk = getattr(skill, "risk", None)
            if capabilities is None or permissions is None or risk not in {"low", "medium", "high"}:
                # Skills without explicit policy metadata are not eligible for
                # workforce dispatch; preserve them in the snapshot as ineligible.
                snapshot[name] = (skill, capabilities, permissions, risk)
            else:
                snapshot[name] = (skill, capabilities, permissions, risk)
        return snapshot

    def _registry_is_unchanged(self) -> bool:
        registry = getattr(self.agent, "registry", None)
        if registry is not self._registry:
            return False
        try:
            if tuple(registry.names()) != tuple(sorted(self._skill_snapshot)):
                return False
            for name, (expected, capabilities, permissions, risk) in self._skill_snapshot.items():
                current = registry.get(name)
                if current is not expected:
                    return False
                if self._declared_set(current, "capabilities") != capabilities:
                    return False
                if self._declared_set(current, "permissions") != permissions:
                    return False
                if getattr(current, "risk", None) != risk:
                    return False
        except Exception:
            return False
        return True

    @staticmethod
    def _declared_set(skill: Any, field: str) -> frozenset[str] | None:
        value = getattr(skill, field, None)
        if not isinstance(value, (tuple, list, set, frozenset)):
            return None
        if any(not isinstance(item, str) or not item.strip() for item in value):
            return None
        return frozenset(value)

    def authorize_and_execute(
        self,
        *,
        employee: Any,
        description: str,
        required_capabilities: tuple[str, ...],
        required_permissions: tuple[str, ...],
        max_autonomy_tier: int,
        task_id: str,
    ) -> WorkforceExecutionOutcome:
        # Defense in depth: the worker has already checked these fields, but
        # the execution adapter treats its inputs as untrusted too.
        if not isinstance(description, str) or not description.strip() or len(description) > 20_000:
            return WorkforceExecutionOutcome(False, "invalid_description", "Task description is invalid.")
        if not isinstance(task_id, str) or not task_id or len(task_id) > 256:
            return WorkforceExecutionOutcome(False, "invalid_task_id", "Task identifier is invalid.")
        if not isinstance(required_capabilities, tuple) or not required_capabilities:
            return WorkforceExecutionOutcome(False, "invalid_capabilities", "Required capabilities are invalid.")
        if not isinstance(required_permissions, tuple):
            return WorkforceExecutionOutcome(False, "invalid_permissions", "Required permissions are invalid.")
        if any(not isinstance(item, str) or not item.strip() for item in required_capabilities):
            return WorkforceExecutionOutcome(False, "invalid_capabilities", "Required capabilities are invalid.")
        if any(not isinstance(item, str) or not item.strip() for item in required_permissions):
            return WorkforceExecutionOutcome(False, "invalid_permissions", "Required permissions are invalid.")
        if type(max_autonomy_tier) is not int or max_autonomy_tier not in range(4):
            return WorkforceExecutionOutcome(False, "invalid_autonomy", "Autonomy limit is invalid.")
        employee_capabilities = getattr(employee, "capabilities", None)
        employee_permissions = getattr(employee, "permissions", None)
        if not isinstance(employee_capabilities, (tuple, list, set, frozenset)):
            return WorkforceExecutionOutcome(False, "employee_policy_invalid", "Employee capability policy is unavailable.")
        if not isinstance(employee_permissions, (tuple, list, set, frozenset)):
            return WorkforceExecutionOutcome(False, "employee_policy_invalid", "Employee permission policy is unavailable.")
        if not set(required_capabilities).issubset(employee_capabilities):
            return WorkforceExecutionOutcome(False, "employee_capability_denied", "Employee lacks a required capability.")
        if not set(required_permissions).issubset(employee_permissions):
            return WorkforceExecutionOutcome(False, "employee_permission_denied", "Employee lacks a required permission.")
        if getattr(employee, "status", "unavailable") != "available":
            return WorkforceExecutionOutcome(False, "employee_unavailable", "Employee is not available.")
        if getattr(employee, "autonomy_tier", 4) > max_autonomy_tier:
            return WorkforceExecutionOutcome(False, "autonomy_denied", "Employee exceeds task autonomy limit.")

        if not self._registry_is_unchanged():
            return WorkforceExecutionOutcome(
                False, "skill_registry_changed",
                "Registered skill inventory or policy metadata changed; rebuild the workforce executor.",
            )

        candidates = []
        for name, (skill, capabilities, permissions, _risk) in self._skill_snapshot.items():
            # Missing metadata is not interpreted as an empty requirement:
            # unknown skill scope fails closed for workforce execution.
            if capabilities is None or permissions is None:
                continue
            if set(required_capabilities).issubset(capabilities) and set(required_permissions).issubset(permissions):
                candidates.append(skill)

        if not candidates:
            return WorkforceExecutionOutcome(
                False,
                "no_scope_compatible_skill",
                "No registered skill explicitly declares the required capabilities and permission scopes.",
            )
        if len(candidates) != 1:
            return WorkforceExecutionOutcome(
                False, "ambiguous_skill_scope",
                "Multiple registered skills match this workforce task; an explicit skill selection is required.",
            )
        selected = candidates[0]

        # run_skill is the existing human approval + guarded invocation path.
        # It does not accept an approval token from the workforce queue.
        response = self.agent.run_skill(selected.name, description)
        if type(getattr(response, "ok", None)) is not bool:
            return WorkforceExecutionOutcome(False, "invalid_agent_response", "Yuki agent returned an invalid response.")
        stage = getattr(response, "stage", None)
        if not isinstance(stage, str) or not stage:
            stage = "execution_failed" if not response.ok else "completed"
        return WorkforceExecutionOutcome(response.ok, stage, str(getattr(response, "message", ""))[:2000])
