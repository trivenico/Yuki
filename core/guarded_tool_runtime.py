"""Guarded dispatch for explicitly registered CLI tools."""
from __future__ import annotations

from typing import Any, Callable


class GuardedToolRuntime:
    def __init__(
        self,
        tools: dict[str, Callable[[str], Any]],
        policy: Any,
        *,
        audit: Callable[[str, str], None] | None = None,
        max_argument_chars: int = 20_000,
        max_result_chars: int = 4_000,
    ):
        if not isinstance(tools, dict):
            raise TypeError("tools must be a dictionary")
        for name, handler in tools.items():
            if not isinstance(name, str) or not name or len(name) > 128:
                raise ValueError("tool names must be non-empty strings of at most 128 characters")
            if not callable(handler):
                raise TypeError("every registered tool must be callable")
        for value, label in (
            (max_argument_chars, "max_argument_chars"),
            (max_result_chars, "max_result_chars"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 1_000_000:
                raise ValueError(f"{label} must be an integer between 1 and 1000000")
        if not callable(getattr(policy, "authorize_tool", None)):
            raise TypeError("policy must provide authorize_tool(name)")
        if audit is not None and not callable(audit):
            raise TypeError("audit must be callable when provided")
        self._tools = dict(tools)
        self._policy = policy
        self._audit = audit
        self._max_argument_chars = max_argument_chars
        self._max_result_chars = max_result_chars

    def _record(self, name: str, outcome: str) -> bool:
        """Persist metadata and report sink failure without leaking arguments."""
        # A missing sink cannot prove that a durable audit record exists.
        # Fail closed before any side-effecting handler runs.
        if self._audit is None:
            return False
        try:
            # Callbacks that historically returned None remain compatible; an
            # explicit False or an exception means durable evidence was not written.
            return self._audit("tool:" + name[:128], outcome) is not False
        except Exception:
            return False

    def invoke(self, name: str, argument: str = "") -> str:
        if not isinstance(name, str) or not name or len(name) > 128:
            self._record("invalid", "invalid_name")
            return "Invalid tool name."
        if not isinstance(argument, str) or len(argument) > self._max_argument_chars:
            self._record(name, "invalid_argument")
            return "Tool argument is invalid or too long."
        if name not in self._tools:
            self._record(name, "not_found")
            return "Unknown tool."
        try:
            authorized = self._policy.authorize_tool(name)
        except Exception:
            authorized = False
        if not authorized:
            self._record(name, "denied")
            return "Tool blocked by Yuki's local security policy."
        # Fail closed before a side effect when the configured audit sink is
        # unavailable. Do not include arguments or results in audit metadata.
        if not self._record(name, "started"):
            return "Tool blocked because audit logging is unavailable."

        try:
            result = str(self._tools[name](argument))
        except Exception:
            if not self._record(name, "failed"):
                return "Tool failed, and audit logging is unavailable."
            return "The tool failed. Check the input and try again."
        if len(result) > self._max_result_chars:
            result = result[: self._max_result_chars] + "… [truncated]"
        if not self._record(name, "completed"):
            return "Tool executed, but audit logging failed. Verify the outcome before retrying."
        return result
