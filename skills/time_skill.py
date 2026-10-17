"""Read-only local clock skill."""
import time
from .base import Skill, SkillResult


class TimeSkill(Skill):
    name = "time"
    description = "Show the current local date and time."

    def run(self, argument: str = "") -> SkillResult:
        return SkillResult(True, time.strftime("%A, %d %B %Y at %H:%M:%S %Z", time.localtime()))
