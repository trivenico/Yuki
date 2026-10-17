"""Bounded, permission-gated tool loop; never exposes arbitrary shell execution."""
from dataclasses import dataclass
from typing import Iterable, Optional

from companion import DEFAULT_PERSONA, EmotionState
from core.permissions import PermissionGate
from core.output_guard import OutputGuard
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
        self.output_guard = OutputGuard()
        self.persona = DEFAULT_PERSONA
        self.emotion = EmotionState()
        self.max_steps = 3

    @property
    def skills(self):
        return {name: self.registry.get(name) for name in self.registry.names()}

    def available_skills(self):
        return self.registry.describe()

    def run_skill(self, name: str, argument: str = "") -> AgentResponse:
        skill = self.registry.get(name)
        if skill is None:
            return AgentResponse(False, "Blocked: that skill is not registered.", name, stage="blocked")
        # Strict policy: request fresh approval for every skill invocation. This is
        # intentionally stricter than per-skill defaults so model output cannot
        # invoke a state-changing or external capability without human confirmation.
        approved = self.permission_gate.request(
            action=f"skill:{skill.name}", reason=f"Invoke {skill.name} ({skill.description}); argument={argument[:500]!r}", risk=skill.risk
        )
        if not approved:
            return AgentResponse(False, "Not executed: explicit human approval was not granted.",
                                 name, stage="permission_denied")
        try:
            result = skill.execute(argument)
            if isinstance(result, dict):
                ok = bool(result.get("ok", True))
                message = str(result.get("message", "Skill completed." if ok else "Skill failed."))
                data = {key: value for key, value in result.items() if key not in {"ok", "message"}}
            else:
                ok = bool(getattr(result, "ok", False))
                message = str(getattr(result, "message", "Skill returned an unsupported result."))
                data = getattr(result, "data", None)
            # Guard both user-visible text and structured skill data. A secret in
            # data must not bypass the message-only output policy.
            data_guard = self.output_guard.check(data) if data is not None else None
            if data_guard is not None and (
                not data_guard.allowed or "output_too_long" in data_guard.reasons
            ):
                data = None
                message = "[Skill output withheld or omitted by safety policy.]"
            message_guard = self.output_guard.check(message)
            message = message_guard.text
            return AgentResponse(ok and message_guard.allowed, message, name, data,
                                 stage="report" if ok and message_guard.allowed else "observe")
        except Exception:
            return AgentResponse(False, "Skill failed safely; detailed exception was not exposed.", name,
                                 stage="observe")
