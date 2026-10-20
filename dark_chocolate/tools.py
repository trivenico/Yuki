"""Central tool registry with identity and risk checks."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable
from .audit import AuditLogger
from .security import ApprovalGate, Risk, requires_approval
@dataclass
class Tool:
    name: str
    description: str
    risk: Risk
    execute: Callable[[dict[str, Any]], str]
    required_access: str = "owner"
class ToolRegistry:
    def __init__(self, audit: AuditLogger, gate: ApprovalGate) -> None:
        self._tools: dict[str, Tool] = {}
        self.audit, self.gate = audit, gate
    def register(self, tool: Tool) -> None:
        if not tool.name.isidentifier() or tool.name in self._tools:
            raise ValueError("tool name must be unique and a valid identifier")
        if tool.risk is Risk.BLOCKED:
            raise ValueError("blocked tools cannot be registered")
        self._tools[tool.name] = tool
    def descriptions(self) -> list[dict[str, str]]:
        return [{"name": t.name, "description": t.description, "risk": t.risk.value} for t in self._tools.values()]
    def execute(self, name: str, arguments: dict[str, Any], access_level: str) -> str:
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError("unknown tool")
        if access_level != "owner" and tool.required_access == "owner":
            self.audit.log("tool_denied", tool=name, reason="access_level")
            raise PermissionError("owner access required")
        if requires_approval(tool.risk) and not self.gate.approve(f"Execute {name} with risk {tool.risk.value}?"):
            self.audit.log("tool_denied", tool=name, reason="approval_denied", risk=tool.risk.value)
            raise PermissionError("human approval denied or unavailable")
        try:
            result = str(tool.execute(arguments))[:20_000]
            self.audit.log("tool_executed", tool=name, arguments=arguments, result=result, risk=tool.risk.value)
            return result
        except Exception as exc:
            self.audit.log("tool_failed", tool=name, error=type(exc).__name__, risk=tool.risk.value)
            raise
