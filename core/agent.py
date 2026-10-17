"""Bounded, permission-gated tool loop. No arbitrary shell or autonomous tool chaining."""
from dataclasses import dataclass
from typing import Iterable, Optional

from companion import DEFAULT_PERSONA, EmotionState
from core.permissions import PermissionGate
from skills import FileSkill, Skill, SystemSkill, TimeSkill


@dataclass(frozen=True)
class AgentResponse:
    ok: bool
    message: str
    tool_name: Optional[str] = None
    data: object = None
    stage: str = "report"


class YukiCoreAgent:
    """One intent -> one allow-listed skill -> explicit approval -> observed result."""

    def __init__(self, skills: Optional[Iterable[Skill]] = None,
                 permission_gate: Optional[PermissionGate] = None):
        available = list(skills) if skills is not None else [TimeSkill(), SystemSkill(), FileSkill()]
        self.skills = {skill.name: skill for skill in available}
        if len(self.skills) != len(available):
            raise ValueError("Skill names must be unique.")
        self.permission_gate = permission_gate or PermissionGate()
        self.persona = DEFAULT_PERSONA
        self.emotion = EmotionState()
        self.max_steps = 3

    def available_skills(self):
        return [{"name": skill.name, "description": skill.description, "risk": skill.risk}
                for skill in self.skills.values()]

    def run_skill(self, name: str, argument: str = "") -> AgentResponse:
        """Plan/check/ask/act/observe/report for exactly one registered skill."""
        skill = self.skills.get(name)
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
