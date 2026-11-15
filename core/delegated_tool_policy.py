"""Permission intersection for delegated/specialist agent tool execution.

A specialist may narrow its parent's authority, never widen it. The parent
policy is consulted on every invocation so revocations take effect immediately.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable
import re

_TOOL_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")


@dataclass(frozen=True)
class DelegatedToolPolicy:
    """Fail-closed intersection of a parent policy and a specialist allowlist."""

    parent_policy: Any = field(repr=False, compare=False)
    allowed_tools: frozenset[str]

    def __init__(self, parent_policy: Any, allowed_tools: Iterable[str]):
        if not callable(getattr(parent_policy, "authorize_tool", None)):
            raise TypeError("parent_policy must provide authorize_tool(name).")
        if isinstance(allowed_tools, (str, bytes)):
            raise TypeError("allowed_tools must be an iterable of tool names, not text.")
        try:
            names = tuple(allowed_tools)
        except TypeError as exc:
            raise TypeError("allowed_tools must be iterable.") from exc
        if any(not isinstance(name, str) or not _TOOL_NAME.fullmatch(name) for name in names):
            raise ValueError("allowed_tools contains an invalid tool name.")
        if len(set(names)) != len(names):
            raise ValueError("allowed_tools must not contain duplicate names.")
        object.__setattr__(self, "parent_policy", parent_policy)
        object.__setattr__(self, "allowed_tools", frozenset(names))

    def authorize_tool(self, name: str) -> bool:
        """Authorize only when both the specialist and current parent allow it."""
        if not isinstance(name, str) or name not in self.allowed_tools:
            return False
        try:
            decision = self.parent_policy.authorize_tool(name)
        except Exception:
            return False
        return decision is True
