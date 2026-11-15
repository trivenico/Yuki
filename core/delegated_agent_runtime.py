"""Secure construction of a specialist's guarded tool protocol.

The builder narrows the registered tool surface to the specialist allowlist and
retains the parent policy as a live authorization check for every invocation.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable

from core.delegated_tool_policy import DelegatedToolPolicy
from core.guarded_tool_runtime import GuardedToolRuntime
from core.structured_tool_protocol import StructuredToolProtocol


def build_delegated_tool_protocol(
    *,
    tools: dict[str, Callable[[str], Any]],
    parent_policy: Any,
    allowed_tools: Iterable[str],
    audit: Callable[[str, str], None] | None = None,
    max_argument_chars: int = 20_000,
    max_result_chars: int = 4_000,
) -> StructuredToolProtocol:
    """Build a protocol whose tool surface and authorization are parent-bounded.

    Every allowed tool must have a registered handler. Unlisted handlers are
    deliberately not passed into the resulting runtime. The parent policy is
    still evaluated on every call, so a later revocation takes effect without
    rebuilding the specialist.
    """
    if not isinstance(tools, dict):
        raise TypeError("tools must be a dictionary")
    policy = DelegatedToolPolicy(parent_policy, allowed_tools)
    missing = policy.allowed_tools.difference(tools)
    if missing:
        raise ValueError("every delegated tool must have a registered handler")
    scoped_tools = {name: tools[name] for name in sorted(policy.allowed_tools)}
    runtime = GuardedToolRuntime(
        scoped_tools,
        policy,
        audit=audit,
        max_argument_chars=max_argument_chars,
        max_result_chars=max_result_chars,
    )
    return StructuredToolProtocol(runtime)


class DelegatedAgentRuntime:
    """Specialist execution facade with a narrowed model-visible tool schema."""

    def __init__(self, loop, tool_definitions):
        from core.agent_execution_loop import AgentExecutionLoop
        if not isinstance(loop, AgentExecutionLoop):
            raise TypeError("loop must be an AgentExecutionLoop")
        self._loop = loop
        self._tool_definitions = tuple(dict(item) for item in tool_definitions)

    @property
    def tool_definitions(self):
        """Return detached definitions so callers cannot widen the runtime."""
        return tuple(dict(item) for item in self._tool_definitions)

    def run(self, user_text: str, **kwargs):
        """Execute with only the specialist's pre-authorized tool definitions."""
        if "tools" in kwargs:
            raise TypeError("specialist tool definitions are fixed at construction")
        return self._loop.run(
            user_text,
            tools=[dict(item) for item in self._tool_definitions],
            **kwargs,
        )


def build_delegated_agent_runtime(
    *,
    model: Callable[..., Any],
    tools: dict[str, Callable[[str], Any]],
    tool_definitions: Iterable[dict[str, Any]],
    parent_policy: Any,
    allowed_tools: Iterable[str],
    stream_model: Callable[..., Any] | None = None,
    loop_options: dict[str, Any] | None = None,
    audit: Callable[[str, str], None] | None = None,
) -> DelegatedAgentRuntime:
    """Construct a model loop where both advertised and executable tools are narrowed.

    The specialist allowlist is applied twice: the provider receives only matching
    function schemas, and the guarded protocol registers only matching handlers.
    Tool definitions are validated before the model loop is constructed.
    """
    from core.agent_execution_loop import AgentExecutionLoop

    policy = DelegatedToolPolicy(parent_policy, allowed_tools)
    definitions = tuple(tool_definitions)
    if any(not isinstance(item, dict) for item in definitions):
        raise TypeError("tool_definitions must contain dictionaries")
    names = []
    for item in definitions:
        if set(item) != {"type", "function"} or item.get("type") != "function":
            raise ValueError("each tool definition must be a function tool")
        function = item.get("function")
        if not isinstance(function, dict) or not isinstance(function.get("name"), str):
            raise ValueError("each function tool must have a name")
        names.append(function["name"])
    if len(set(names)) != len(names):
        raise ValueError("tool definition names must be unique")
    if set(names) != set(tools):
        raise ValueError("tool definitions and registered handlers must match exactly")
    missing = policy.allowed_tools.difference(names)
    if missing:
        raise ValueError("every delegated tool must have a model definition")
    scoped_definitions = tuple(
        item for item in definitions
        if item["function"]["name"] in policy.allowed_tools
    )
    protocol = build_delegated_tool_protocol(
        tools=tools,
        parent_policy=parent_policy,
        allowed_tools=policy.allowed_tools,
        audit=audit,
    )
    options = {} if loop_options is None else dict(loop_options)
    if not isinstance(options, dict):
        raise TypeError("loop_options must be a dictionary")
    loop = AgentExecutionLoop(
        model,
        protocol,
        stream_model=stream_model,
        **options,
    )
    return DelegatedAgentRuntime(loop, scoped_definitions)
