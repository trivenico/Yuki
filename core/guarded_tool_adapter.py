"""Adapt Yuki's existing allow-listed terminal tools to the guarded skill runtime."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from skills.base import SkillResult
from yuki_tools import TOOLS


@dataclass(frozen=True)
class ToolRisk:
    risk: str
    requires_approval: bool


_TOOL_RISKS = {
    "time": ToolRisk("low", False),
    "list_files": ToolRisk("low", False),
    "calculate": ToolRisk("low", False),
    # Appends user-provided content to a workspace file.
    "save_note": ToolRisk("medium", True),
}


class GuardedTool:
    """Skill-compatible wrapper for one existing explicitly allow-listed tool."""

    def __init__(self, name: str, function: Callable[[str], object], risk: ToolRisk):
        self.name = name
        self.description = f"Existing allow-listed Yuki tool: {name}"
        self.risk = risk.risk
        self.requires_approval = risk.requires_approval
        self._function = function

    def execute(self, argument: str = "") -> SkillResult:
        try:
            result = self._function(argument)
        except Exception:
            return SkillResult(False, "Tool execution failed safely.")
        if not isinstance(result, (str, int, float, bool)):
            return SkillResult(False, "Tool returned an unsupported result type.")
        return SkillResult(True, str(result)[:2000])


class GuardedToolRegistry:
    """Read-only adapter; exposes only names present in Yuki's TOOLS allowlist."""

    def __init__(self):
        self._tools = {
            name: GuardedTool(name, function, _TOOL_RISKS.get(name, ToolRisk("high", True)))
            for name, function in TOOLS.items()
        }

    def get(self, name: str):
        return self._tools.get(name)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))
