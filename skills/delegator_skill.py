"""Sub-Agent Delegation Skill for Yuki."""

from skills.base import Skill, SkillResult
from core.agent_delegator import AgentDelegator


class DelegatorSkill(Skill):
    """Skill to delegate complex goals across specialized sub-agent roles."""

    name = "agent_delegator"
    description = "Delegates sub-tasks across specialized agent roles (researcher, coder, auditor)."
    risk = "medium"
    requires_approval = True

    def __init__(self, provider=None):
        self.delegator = AgentDelegator(provider=provider)

    def run(self, argument: str = "") -> SkillResult:
        goal = argument
        roles = None
        if "::" in argument:
            parts = argument.split("::", 1)
            goal = parts[0].strip()
            roles = [r.strip() for r in parts[1].split(",") if r.strip()]

        res = self.delegator.delegate_task(goal, roles=roles)
        if "error" in res:
            return SkillResult(False, res["error"])
        return SkillResult(True, res["synthesis"], data=res)
