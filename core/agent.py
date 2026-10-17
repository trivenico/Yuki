"""Bounded, permission-gated tool loop; never exposes arbitrary shell execution."""
from dataclasses import dataclass
from typing import Iterable, Optional

from companion import DEFAULT_PERSONA, EmotionState
from core.permissions import PermissionGate
from core.output_guard import OutputGuard
from core.skill_dispatch import SkillDispatcher
from core.telemetry import Telemetry
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
                 registry: Optional[SkillRegistry] = None,
                 telemetry: Optional[Telemetry] = None,
                 dispatcher: Optional[SkillDispatcher] = None):
        if registry is not None and skills is not None:
            raise ValueError("Pass either skills or registry, not both.")
        if registry is not None:
            self.registry = registry
        else:
            available = list(skills) if skills is not None else [TimeSkill(), SystemSkill(), FileSkill()]
            self.registry = SkillRegistry(available)
        self.permission_gate = permission_gate or PermissionGate()
        self.output_guard = OutputGuard()
        self.telemetry = telemetry if telemetry is not None else Telemetry(enabled=True)
        self.dispatcher = dispatcher
        self.persona = DEFAULT_PERSONA
        self.emotion = EmotionState()
        self.max_steps = 3

    @property
    def skills(self):
        return {name: self.registry.get(name) for name in self.registry.names()}

    def available_skills(self):
        return self.registry.describe()

    def run_skill(self, name: str, argument: str = "") -> AgentResponse:
        """Execute a skill and record redacted timing/status metadata."""
        with self.telemetry.span("agent.run_skill", attributes={"skill_name": name}) as span:
            response = self._run_skill(name, argument)
            attributes = {
                "skill_name": name,
                "result_ok": response.ok,
                "result_stage": response.stage,
            }
            for key, value in attributes.items():
                if hasattr(span, "set_attribute"):
                    span.set_attribute(key, value)
                elif isinstance(span, dict) and callable(span.get("set_attribute")):
                    span["set_attribute"](key, value)
            return response

    def dispatch_manifest_skill(self, skill_id: str, arguments: dict, *,
                                approval_token: str | None = None,
                                actor: str | None = None,
                                session_id: str | None = None) -> AgentResponse:
        """Run a manifest-backed skill through the policy dispatcher.

        Consequential actions require a pre-issued, action-bound approval token.
        This method never prompts for or fabricates consent.
        """
        with self.telemetry.span("agent.dispatch_manifest_skill",
                                attributes={"skill_id": skill_id}) as span:
            if self.dispatcher is None:
                response = AgentResponse(False, "Blocked: the policy dispatcher is not configured.",
                                         skill_id, stage="blocked")
            else:
                result = self.dispatcher.dispatch(
                    skill_id, arguments, approval_token=approval_token,
                    actor=actor, session_id=session_id,
                )
                if not result.ok:
                    response = AgentResponse(
                        False, f"Skill not executed ({result.code}).", skill_id,
                        stage="blocked" if result.code in {
                            "skill_not_registered", "capability_denied",
                            "approval_token_required", "approval_invalid_or_expired",
                            "invalid_input",
                        } else "observe",
                    )
                else:
                    data_guard = self.output_guard.check(result.output)
                    if not data_guard.allowed or "output_too_long" in data_guard.reasons:
                        response = AgentResponse(
                            False, "[Skill output withheld by safety policy.]",
                            skill_id, stage="observe",
                        )
                    else:
                        response = AgentResponse(True, "Skill completed successfully.",
                                                  skill_id, result.output, stage="report")
            for key, value in (("result_ok", response.ok), ("result_stage", response.stage)):
                if hasattr(span, "set_attribute"):
                    span.set_attribute(key, value)
                elif isinstance(span, dict) and callable(span.get("set_attribute")):
                    span["set_attribute"](key, value)
            return response

    def _run_skill(self, name: str, argument: str = "") -> AgentResponse:
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
