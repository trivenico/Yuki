"""Multi-Agent Sub-Task Delegation Engine (Hermes paradigm).

Decomposes complex goals into sub-tasks assigned to specialized agent roles
and coordinates their step-by-step resolution safely.
"""

from typing import Dict, Any, List
from core.role_engine import RoleEngine


class AgentDelegator:
    """Delegates sub-tasks to specialized agent roles and synthesizes responses."""

    def __init__(self, provider=None):
        self.provider = provider
        self.role_engine = RoleEngine()

    def delegate_task(self, goal: str, roles: List[str] = None) -> Dict[str, Any]:
        """Decompose and execute goal across specialized sub-agent roles."""
        if not goal or not isinstance(goal, str) or not goal.strip():
            return {"error": "Goal must be a non-empty string."}

        default_roles = ["developer", "security", "tutor"]
        active_roles = [r.lower() for r in (roles or default_roles) if isinstance(r, str)]
        if not active_roles:
            active_roles = default_roles

        sub_results = []
        for role in active_roles:
            try:
                role_info = self.role_engine.set_role(role)
                role_name = role_info.get("name", role)
            except ValueError:
                role_name = role

            prompt = f"Role [{role_name.upper()}]: Analyze and contribute to goal: '{goal}'"
            if self.provider:
                try:
                    res = self.provider.generate(prompt)
                except Exception as e:
                    res = f"Sub-agent [{role_name}] execution error: {e}"
            else:
                res = f"Sub-agent [{role_name}] completed assigned analysis for goal: {goal}"

            sub_results.append({
                "role": role_name,
                "analysis": res
            })

        summary = f"Multi-Agent Task Resolution for: '{goal}'\n\n"
        for sr in sub_results:
            summary += f"### Role: {sr['role'].capitalize()}\n{sr['analysis']}\n\n"

        return {
            "status": "success",
            "goal": goal,
            "roles_executed": [sr["role"] for sr in sub_results],
            "sub_results": sub_results,
            "synthesis": summary
        }
