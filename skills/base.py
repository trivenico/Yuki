"""Base class for narrowly scoped, permissioned skills."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SkillResult:
    ok: bool
    message: str
    data: Any = None


class Skill(ABC):
    name = "unnamed"
    description = "No description provided."
    risk = "low"
    requires_approval = True

    @abstractmethod
    def run(self, argument: str = "") -> SkillResult:
        """Perform one bounded operation; implementations must validate inputs."""

    def execute(self, argument: str = "") -> SkillResult:
        if not isinstance(argument, str):
            return SkillResult(False, "Argument must be text.")
        try:
            result = self.run(argument[:500])
            return (
                result
                if isinstance(result, SkillResult)
                else SkillResult(False, "Skill returned an invalid result.")
            )
        except Exception as exc:
            return SkillResult(False, f"Skill failed safely ({type(exc).__name__}).")
