"""Bounded, permission-gated tool loop; never exposes arbitrary shell execution."""
from dataclasses import dataclass
from typing import Iterable, Optional

from companion import DEFAULT_PERSONA, EmotionState
from core.permissions import PermissionGate
from skills import FileSkill, Skill, SystemSkill, TimeSkill
from skills.registry import SkillRegistry


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
        self.persona = DEFAULT_PERSONA
        self.emotion = EmotionState()
        self.max_steps = 3

    @property
    def skills(self):
        """Compatibility mapping for older callers; registry remains authoritative."""
        return {name: self.registry.get(name) for name in self.registry.names()}

    def available_skills(self):
        return self.registry.describe()

    def run_skill(self, name: str, argument: str = "") -> AgentResponse:
        """Run exactly one registered skill; never chain or infer tool calls."""
        skill = self.registry.get(name)
        if skill is None:
            return AgentResponse(False, "Blocked: that skill is not registered.", name, stage="blocked")
        if skill.requires_approval:
            approved = self.permission_gate.request(
                action=f"skill:{skill.name}",
                reason=f"User requested: {skill.description}",
                risk=skill.risk,
            )
            if not approved:
                return AgentResponse(False, "Not executed: explicit human approval was not granted.",
                                     name, stage="permission_denied")
        result = skill.execute(argument)
        if not result.ok:
            return AgentResponse(False, result.message, name, result.data, stage="observe")
        return AgentResponse(True, result.message, name, result.data, stage="report")
