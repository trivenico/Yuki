"""Permission-scoped tool gateway for specialist agents.

Only explicitly registered tools can run. Tool handlers are trusted application
code; agent-supplied arguments are validated and bounded before invocation.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Callable, Mapping

_TOOL_ID = re.compile(r"^[a-z][a-z0-9_.-]{0,79}$")
_MAX_ARGUMENT_BYTES = 16 * 1024
_MAX_RESULT_BYTES = 32 * 1024
_MAX_TOOLS_PER_AGENT = 32


class AgentToolError(ValueError):
    """A tool request is invalid, unauthorized, or exceeds configured bounds."""


@dataclass(frozen=True)
class AgentTool:
    name: str
    permission: str
    handler: Callable[..., Any]
    description: str
    enabled: bool = True


class SpecialistToolGateway:
    """Expose a small, explicit tool allow-list to authorized specialist agents."""

    def __init__(
        self,
        tools: tuple[AgentTool, ...] | list[AgentTool],
        *,
        authorize: Callable[[str, str, str], bool],
    ) -> None:
        if not callable(authorize):
            raise TypeError("a trusted authorization callback is required.")
        if not isinstance(tools, (tuple, list)) or len(tools) > _MAX_TOOLS_PER_AGENT:
            raise ValueError("tools must be a bounded list of at most 32 entries.")
        registry: dict[str, AgentTool] = {}
        for tool in tools:
            if not isinstance(tool, AgentTool):
                raise TypeError("every registered tool must be an AgentTool.")
            if not isinstance(tool.name, str) or not _TOOL_ID.fullmatch(tool.name):
                raise ValueError("tool names must be bounded lowercase identifiers.")
            if tool.name in registry:
                raise ValueError("tool names must be unique.")
            if not isinstance(tool.permission, str) or not re.fullmatch(
                r"[A-Za-z][A-Za-z0-9_.:-]{0,99}", tool.permission
            ):
                raise ValueError("tool permission must be a bounded permission identifier.")
            if not callable(tool.handler):
                raise TypeError("tool handler must be callable.")
            if not isinstance(tool.description, str) or not tool.description.strip() or len(tool.description) > 500:
                raise ValueError("tool description must be non-empty bounded text.")
            if type(tool.enabled) is not bool:
                raise ValueError("enabled must be a boolean.")
            registry[tool.name] = tool
        self._tools = registry
        self._authorize = authorize

    def list_tools(self, *, actor_id: str, agent_id: str, permissions: frozenset[str]) -> tuple[dict[str, str], ...]:
        self._check_identity(actor_id, agent_id)
        if not isinstance(permissions, frozenset):
            raise ValueError("permissions must be a frozenset.")
        visible = []
        for tool in self._tools.values():
            if tool.enabled and tool.permission in permissions and self._allowed(
                actor_id, "agent:tool:list", agent_id
            ):
                visible.append({"name": tool.name, "permission": tool.permission,
                                "description": tool.description})
        return tuple(visible)

    def invoke(
        self, *, actor_id: str, agent_id: str, permissions: frozenset[str],
        tool_name: str, arguments: Mapping[str, Any],
    ) -> Any:
        self._check_identity(actor_id, agent_id)
        if not isinstance(tool_name, str) or not _TOOL_ID.fullmatch(tool_name):
            raise AgentToolError("invalid tool name.")
        tool = self._tools.get(tool_name)
        if tool is None or not tool.enabled:
            raise AgentToolError("tool is not available.")
        if not isinstance(permissions, frozenset) or tool.permission not in permissions:
            raise PermissionError("specialist agent lacks the required tool permission.")
        if not self._allowed(actor_id, "agent:tool:invoke", agent_id):
            raise PermissionError("actor is not authorized to invoke specialist tools.")
        if not isinstance(arguments, Mapping):
            raise AgentToolError("tool arguments must be an object.")
        try:
            encoded = json.dumps(arguments, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise AgentToolError("tool arguments must be JSON-compatible.") from exc
        if len(encoded.encode("utf-8")) > _MAX_ARGUMENT_BYTES:
            raise AgentToolError("tool arguments exceed the configured size limit.")
        result = tool.handler(**dict(arguments))
        try:
            result_json = json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise AgentToolError("tool returned a non-JSON-compatible result.") from exc
        if len(result_json.encode("utf-8")) > _MAX_RESULT_BYTES:
            raise AgentToolError("tool result exceeds the configured size limit.")
        return result

    @staticmethod
    def _check_identity(actor_id: str, agent_id: str) -> None:
        if not isinstance(actor_id, str) or not actor_id.strip() or len(actor_id) > 200 or "\x00" in actor_id:
            raise ValueError("actor_id must be bounded non-empty text.")
        if not isinstance(agent_id, str) or not _TOOL_ID.fullmatch(agent_id):
            raise ValueError("agent_id must be a bounded identifier.")

    def _allowed(self, actor_id: str, action: str, agent_id: str) -> bool:
        try:
            return self._authorize(actor_id.strip(), action, agent_id) is True
        except Exception:
            return False
