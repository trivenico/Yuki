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
        return {name: self.registry.get(name) for name in self.registry.names()}

    def available_skills(self):
        return self.registry.describe()

    def run_skill(self, name: str, argument: str = "") -> AgentResponse:
        # Keep the direct public entry point at least as strict as the model planner.
        # Approval UI and execution must see the same bounded argument, not a truncated
        # preview of an unbounded value.
        if not isinstance(argument, str) or len(argument) > 500 or "\x00" in argument or "\\x00" in argument:
            return AgentResponse(
                False,
                "Blocked: skill argument must be a NUL-free string of at most 500 characters.",
                name if isinstance(name, str) else None,
                stage="invalid_input",
            )
        if not isinstance(name, str) or not name.strip():
            return AgentResponse(False, "Blocked: skill name is invalid.", stage="invalid_input")
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
                ok = result.get("ok")
                if type(ok) is not bool:
                    return AgentResponse(
                        False,
                        "Skill returned an invalid result contract; success must be an explicit boolean.",
                        name,
                        {"error": "invalid_result_contract"},
                        stage="observe",
                    )
                message = str(result.get("message", "Skill completed." if ok else "Skill failed."))
                data = {key: value for key, value in result.items() if key not in {"ok", "message"}}
            else:
                ok = getattr(result, "ok", None)
                if type(ok) is not bool:
                    return AgentResponse(
                        False,
                        "Skill returned an invalid result contract; success must be an explicit boolean.",
                        name,
                        {"error": "invalid_result_contract"},
                        stage="observe",
                    )
                message = str(getattr(result, "message", "Skill completed." if ok else "Skill failed."))
                data = getattr(result, "data", None)
            return AgentResponse(ok, message, name, data, stage="report" if ok else "observe")
        except Exception:
            return AgentResponse(False, "Skill failed safely; detailed exception was not exposed.", name,
                                 stage="observe")
