"""Bounded, permission-gated tool loop; never exposes arbitrary shell execution."""
from dataclasses import dataclass
import json
from typing import Iterable, Optional

from companion import DEFAULT_PERSONA, EmotionState
from core.permissions import PermissionGate
from core.tool_authorization import authorize_tool_call
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
                 registry: Optional[SkillRegistry] = None, dispatcher=None):
        if registry is not None and skills is not None:
            raise ValueError("Pass either skills or registry, not both.")
        if registry is not None:
            self.registry = registry
        else:
            available = list(skills) if skills is not None else [TimeSkill(), SystemSkill(), FileSkill()]
            self.registry = SkillRegistry(available)
        # Manifest-dispatched skills are explicitly registered and execute through the
        # subprocess dispatcher; the legacy in-process registry remains separate.
        self.dispatcher = dispatcher
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
        skill = self.registry.get(name)
        if skill is None:
            return AgentResponse(False, "Blocked: that skill is not registered.", name, stage="blocked")
        if not isinstance(argument, str) or len(argument) > 500:
            return AgentResponse(False, "Blocked: skill argument must be text of at most 500 characters.",
                                 name, stage="blocked")
        # Freeze the exact invocation payload before authorization. All skill calls
        # require approval here; skill metadata cannot enable implicit autonomy.
        try:
            approved_snapshot = json.dumps(
                {"argument": argument}, ensure_ascii=False, allow_nan=False, separators=(",", ":")
            )
            approval_args = json.loads(approved_snapshot)
        except (TypeError, ValueError):
            return AgentResponse(False, "Blocked: invalid skill arguments.", name, stage="blocked")
        def request_approval(tool_name, risk, args):
            return self.permission_gate.request(
                action=f"skill:{tool_name}",
                reason=f"Invoke {tool_name} ({skill.description}); argument={args['argument']!r}",
                risk=risk,
            )
        allowed = authorize_tool_call(
            skill.name, skill.risk, approval_args,
            approval_callback=request_approval, auto_approve=False,
        )
        if not allowed:
            return AgentResponse(False, "Not executed: explicit human approval was not granted.",
                                 name, stage="permission_denied")
        try:
            execution_args = json.loads(approved_snapshot)
            result = skill.execute(execution_args["argument"])
            if isinstance(result, dict):
                ok = bool(result.get("ok", True))
                message = str(result.get("message", "Skill completed." if ok else "Skill failed."))
                data = {key: value for key, value in result.items() if key not in {"ok", "message"}}
            else:
                ok = bool(getattr(result, "ok", False))
                message = str(getattr(result, "message", "Skill returned an unsupported result."))
                data = getattr(result, "data", None)
            return AgentResponse(ok, message, name, data, stage="report" if ok else "observe")
        except Exception:
            return AgentResponse(False, "Skill failed safely; detailed exception was not exposed.", name,
                                 stage="observe")
