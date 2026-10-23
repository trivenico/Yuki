"""Guarded dispatch for Yuki's explicitly registered CLI tools.

This boundary complements SecurityPolicy: policy authorization is checked before
dispatch, arguments and results are bounded, and audit events never contain
argument or result contents. Tool implementations themselves must remain
least-privileged and independently safe.
"""
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
        self._tools = dict(tools)
        self._policy = policy
        self._audit = audit
        self._max_argument_chars = max_argument_chars
        self._max_result_chars = max_result_chars

    def _record(self, name: str, outcome: str) -> None:
        if self._audit is not None:
            try:
                self._audit("tool:" + name[:128], outcome)
            except Exception:
                # A telemetry failure must not bypass or broaden authorization.
                pass

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
        try:
            result = str(self._tools[name](argument))
        except Exception:
            self._record(name, "failed")
            return "The tool failed. Check the input and try again."
        if len(result) > self._max_result_chars:
            result = result[: self._max_result_chars] + "… [truncated]"
        self._record(name, "completed")
        return result
