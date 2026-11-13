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
        require_audit: bool = False,
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
        if type(require_audit) is not bool:
            raise ValueError("require_audit must be a boolean")
        if require_audit and audit is None:
            raise ValueError("required audit mode needs an audit sink")
        self._tools = dict(tools)
        self._policy = policy
        self._audit = audit
        self._require_audit = require_audit
        self._max_argument_chars = max_argument_chars
        self._max_result_chars = max_result_chars

    @property
    def authorization_policy(self):
        """Return the policy enforcing authorization for this guarded runtime."""
        return self._policy

    @property
    def audit_callback(self):
        """Return the configured audit sink for owner-isolation validation."""
        return self._audit

    @property
    def audit_required(self) -> bool:
        """Whether every executable invocation is gated on durable audit writes."""
        return self._require_audit

    @property
    def registered_tool_names(self) -> frozenset[str]:
        """Return the immutable set of handlers executable by this runtime."""
        return frozenset(self._tools)

    def _record(self, name: str, outcome: str) -> bool:
        if self._audit is None:
            return not self._require_audit
        try:
            self._audit("tool:" + name[:128], outcome)
            return True
        except Exception:
            # Optional telemetry is best-effort. Required audit is an execution
            # control and must fail closed when the sink cannot persist an event.
            return False

    def invoke(self, name: str, argument: str = "") -> str:
        if not isinstance(name, str) or not name or len(name) > 128:
            recorded = self._record("invalid", "invalid_name")
            if self._require_audit and not recorded:
                return "Tool blocked because its required audit record could not be written."
            return "Invalid tool name."
        if not isinstance(argument, str) or len(argument) > self._max_argument_chars:
            recorded = self._record(name, "invalid_argument")
            if self._require_audit and not recorded:
                return "Tool blocked because its required audit record could not be written."
            return "Tool argument is invalid or too long."
        if name not in self._tools:
            recorded = self._record(name, "not_found")
            if self._require_audit and not recorded:
                return "Tool blocked because its required audit record could not be written."
            return "Unknown tool."
        try:
            authorized = self._policy.authorize_tool(name)
        except Exception:
            authorized = False
        if not authorized:
            recorded = self._record(name, "denied")
            if self._require_audit and not recorded:
                return "Tool blocked because its required denial audit record could not be written."
            return "Tool blocked by Yuki's local security policy."
        if self._require_audit and not self._record(name, "authorized"):
            return "Tool blocked because its required audit record could not be written."
        try:
            result = str(self._tools[name](argument))
        except Exception:
            recorded = self._record(name, "failed")
            if self._require_audit and not recorded:
                return "Tool failed and its audit record could not be written; inspect activity before retrying."
            return "The tool failed. Check the input and try again."
        if len(result) > self._max_result_chars:
            result = result[: self._max_result_chars] + "… [truncated]"
        if not self._record(name, "completed") and self._require_audit:
            return "Tool execution may have completed, but its audit record failed; inspect activity before retrying."
        return result
