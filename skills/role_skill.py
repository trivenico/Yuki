"""Skill for listing and switching conversational role profiles."""
from skills.base import Skill, SkillResult
from core.role_engine import RoleEngine

class RoleSkill(Skill):
    name = "role"
    description = "Show or switch Yuki's conversational role profile. Roles do not change permissions."
    risk = "low"
    requires_approval = False
    def __init__(self, engine=None):
        self.engine = engine or RoleEngine()
    def run(self, argument=""):
        command = argument.strip()
        if not command or command.lower() in {"list", "help"}:
            return SkillResult(True, "Available role profiles.", {"current": self.engine.current(), "roles": self.engine.available()})
        if command.lower() == "current":
            return SkillResult(True, "Current role profile.", self.engine.current())
        try:
            result = self.engine.set_role(command)
        except ValueError as exc:
            return SkillResult(False, str(exc))
        return SkillResult(True, "Conversational role updated. Tool permissions are unchanged.", result)
