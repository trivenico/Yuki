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
