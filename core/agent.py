"""Bounded, permission-gated tool loop; never exposes arbitrary shell execution."""
from dataclasses import dataclass
import hashlib
from typing import Iterable, Optional

from companion import DEFAULT_PERSONA, EmotionState
from core.permissions import PermissionGate
from core.native_intelligence import NativeIntentRouter
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
        self.native_intelligence = NativeIntentRouter()

    @property
    def skills(self):
        return {name: self.registry.get(name) for name in self.registry.names()}

    def available_skills(self):
        return self.registry.describe()

    def _resolve_task_intent(self, task_query: str):
        catalog = SkillCatalog(self.registry)
        cards = tuple(
            card for name in self.registry.names()
            if (card := catalog.get(name)) is not None
        )
        return self.native_intelligence.resolve(task_query, cards), cards

    def discover_skills(self, task_query: str, *, limit: int = 5):
        """Rank native intent matches without executing or authorizing a skill."""
        if isinstance(limit, bool) or not isinstance(limit, int):
            limit = 5
        limit = max(1, min(limit, 25))
        resolution, cards = self._resolve_task_intent(task_query)
        by_name = {card.name: card for card in cards}
        names = []
        if resolution.selected is not None:
            names.append(resolution.selected.name)
        names.extend(item.name for item in resolution.alternatives)
        return tuple(by_name[name] for name in names[:limit] if name in by_name)

    def run_skill_for_task(self, task_query: str, argument: str = "") -> AgentResponse:
        """Resolve intent locally, abstain on ambiguity, then require approval."""
        resolution, _cards = self._resolve_task_intent(task_query)
        if resolution.status == "ambiguous":
            choices = ", ".join(item.name for item in resolution.alternatives[:3])
            return AgentResponse(
                False,
                "I found multiple possible capabilities. Please clarify which one to use: " + choices,
                stage="intent_ambiguous",
            )
        if resolution.selected is None:
            return AgentResponse(
                False,
                "No registered skill confidently matches this task; clarify the request or choose a skill.",
                stage="skill_not_found",
            )
        return self.run_skill(resolution.selected.name, argument)

    def run_skill(self, name: str, argument: str = "") -> AgentResponse:
        if not isinstance(argument, str) or len(argument) > 20000:
            return AgentResponse(
                False,
                "Blocked: skill arguments must be text no longer than 20000 characters.",
                name if isinstance(name, str) else None,
                stage="invalid_argument",
            )
        try:
            argument_bytes = argument.encode("utf-8")
        except UnicodeEncodeError:
            return AgentResponse(
                False, "Blocked: skill arguments must be valid UTF-8 text.",
                name if isinstance(name, str) else None, stage="invalid_argument",
            )
        skill = self.registry.get(name)
        if skill is None:
            return AgentResponse(False, "Blocked: that skill is not registered.", name, stage="blocked")
        # Strict policy: request fresh approval for every skill invocation. This is
        # intentionally stricter than per-skill defaults so model output cannot
        # invoke a state-changing or external capability without human confirmation.
        argument_fingerprint = hashlib.sha256(argument_bytes).hexdigest()
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
            expected_skill=skill,
        )
        return AgentResponse(
            result.ok,
            result.message,
            name,
            result.data,
            stage="report" if result.ok else "observe",
        )
