"""Bounded, permission-gated tool loop; never exposes arbitrary shell execution."""
from dataclasses import dataclass
import hashlib
from typing import Iterable, Optional

from companion import DEFAULT_PERSONA, EmotionState
from core.permissions import PermissionGate
from core.guarded_skill_runtime import OneShotSkillApprovals, invoke_registered_skill
from skills import FileSkill, Skill, SystemSkill, TimeSkill
from skills.registry import SkillRegistry
from skills.catalog import SkillCatalog


@dataclass(frozen=True)
class AgentResponse:
    ok: bool
    message: str
    tool_name: Optional[str] = None
    data: object = None
    stage: str = "report"


class YukiCoreAgent:
    """One intent -> registered skill -> explicit approval -> observed result."""

    def __init__(self, skills: Optional[Iterable[Skill]] = None,
                 permission_gate: Optional[PermissionGate] = None,
                 registry: Optional[SkillRegistry] = None):
        if registry is not None and skills is not None:
            raise ValueError("Pass either skills or registry, not both.")
        if registry is not None:
            self.registry = registry
        else:
            available = list(skills) if skills is not None else [TimeSkill(), SystemSkill(), FileSkill()]
            self.registry = SkillRegistry(available)
        self.permission_gate = permission_gate or PermissionGate()
        self.invocation_approvals = OneShotSkillApprovals()
        self.persona = DEFAULT_PERSONA
        self.emotion = EmotionState()
        self.max_steps = 3

    @property
    def skills(self):
        return {name: self.registry.get(name) for name in self.registry.names()}

    def available_skills(self):
        return self.registry.describe()

    def discover_skills(self, task_query: str, *, limit: int = 5):
        """Find relevant registered skills without executing or authorizing them."""
        return SkillCatalog(self.registry).search(task_query, limit=limit)

    def run_skill_for_task(self, task_query: str, argument: str = "") -> AgentResponse:
        """Select the best catalog match, then use the normal approval-gated path.

        Empty queries and missing matches fail closed. Catalog ranking is discovery,
        not authorization; PermissionGate remains the final approval boundary.
        """
        cards = self.discover_skills(task_query, limit=5)
        if not cards:
            return AgentResponse(
                False,
                "No registered skill matches this task; clarify the request or choose a skill.",
                stage="skill_not_found",
            )
        selected = cards[0]
        return self.run_skill(selected.name, argument)

    def run_skill(self, name: str, argument: str = "") -> AgentResponse:
        skill = self.registry.get(name)
        if skill is None:
            return AgentResponse(False, "Blocked: that skill is not registered.", name, stage="blocked")
        # Strict policy: request fresh approval for every skill invocation. This is
        # intentionally stricter than per-skill defaults so model output cannot
        # invoke a state-changing or external capability without human confirmation.
        argument_fingerprint = hashlib.sha256(argument.encode("utf-8")).hexdigest()
        approved = self.permission_gate.request(
            action=f"skill:{skill.name}:sha256:{argument_fingerprint}",
            reason=(
                f"Invoke {skill.name} ({skill.description}); arguments are redacted. "
                f"Argument SHA-256: {argument_fingerprint}."
            ),
            risk=skill.risk,
        )
        if not approved:
            return AgentResponse(False, "Not executed: explicit human approval was not granted.",
                                 name, stage="permission_denied")
        risk = str(getattr(skill, "risk", "high")).lower()
        needs_guard_approval = (
            bool(getattr(skill, "requires_approval", True))
            or risk not in {"low", "medium", "high", "critical"}
            or risk in {"high", "critical"}
        )
        if needs_guard_approval:
            self.invocation_approvals.approve(name, argument)
        result = invoke_registered_skill(
            self.registry,
            name,
            argument,
            approvals=self.invocation_approvals,
        )
        return AgentResponse(
            result.ok,
            result.message,
            name,
            result.data,
            stage="report" if result.ok else "observe",
        )
