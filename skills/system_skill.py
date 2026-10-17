"""Read-only runtime information; never executes shell commands."""
import platform
import sys
from .base import Skill, SkillResult


class SystemSkill(Skill):
    name = "system"
    description = "Show Python and operating-system details."

    def run(self, argument: str = "") -> SkillResult:
        details = {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        }
        return SkillResult(True, "Read-only system details collected.", details)
