"""Bounded, permission-gated tool loop; never exposes arbitrary shell execution."""
from dataclasses import dataclass
import json
from typing import Iterable, Optional

from companion import DEFAULT_PERSONA, EmotionState
from core.permissions import PermissionGate
from core.invocation_facade import InvocationFacade
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

    def invoke_manifest_skill(self, skill_id: str, arguments: dict, *, confirm) -> AgentResponse:
        """Invoke only a registered manifest skill through the shared review facade."""
        import os
        if os.environ.get("YUKI_ENV", "development").strip().lower() == "production":
            return AgentResponse(
                False,
                "Blocked: production manifest execution remains disabled until a verified OS sandbox is enabled.",
                skill_id, stage="blocked",
            )
        if self.dispatcher is None:
            return AgentResponse(
                False, "Blocked: no manifest skill dispatcher is configured.",
                skill_id, stage="blocked",
            )
        try:
            facade = InvocationFacade(self.dispatcher)
            result = facade.invoke(skill_id, arguments, confirm=confirm)
            ok = result.get("ok") is True
            status = str(result.get("status", "completed" if ok else "failed"))
            message = "Manifest skill completed." if ok else f"Manifest skill not executed ({status})."
            data = {key: value for key, value in result.items() if key not in {"ok", "status", "message"}}
            return AgentResponse(ok, message, skill_id, data, stage="report" if ok else status)
        except Exception:
            # Do not expose internal errors or approval tokens to callers.
            return AgentResponse(
                False, "Manifest skill invocation failed safely.",
                skill_id, stage="blocked",
            )

    def run_skill(self, name: str, argument: str = "") -> AgentResponse:
        # Production must not reach legacy in-process skill implementations.
        # The manifest dispatcher/facade is not yet the universal runtime path,
        # and no verified OS sandbox backend is shipped, so fail closed here.
        import os
        if os.environ.get("YUKI_ENV", "development").strip().lower() == "production":
            return AgentResponse(
                False,
                "Blocked: production skill execution is disabled until the runtime "
                "uses the reviewed invocation path and a verified OS sandbox.",
                name,
                stage="blocked",
            )
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
