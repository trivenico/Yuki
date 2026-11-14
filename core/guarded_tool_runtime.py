"""Guarded dispatch for explicitly registered CLI tools."""
from __future__ import annotations

from typing import Any, Callable
import inspect


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

    def _record(self, name: str, outcome: str) -> None:
        if self._audit is not None:
            try:
                self._audit("tool:" + name[:128], outcome)
            except Exception:
                # Telemetry failure must not bypass or broaden authorization.
                pass

    def invoke(self, name: str, argument: str = "") -> str:
        output, _status = self.invoke_with_status(name, argument)
        return output

    def invoke_with_status(
        self, name: str, argument: str = "", *, idempotency_key: str | None = None,
    ) -> tuple[str, str]:
        """Invoke a guarded tool and expose a conservative side-effect outcome.

        Handler exceptions are uncertain: a remote side effect may have committed
        before the handler failed to receive or return its acknowledgement.
        """
        if not isinstance(name, str) or not name or len(name) > 128:
            self._record("invalid", "invalid_name")
            return "Invalid tool name.", "failed"
        if not isinstance(argument, str) or len(argument) > self._max_argument_chars:
            self._record(name, "invalid_argument")
            return "Tool argument is invalid or too long.", "failed"
        if name not in self._tools:
            self._record(name, "not_found")
            return "Unknown tool.", "failed"
        try:
            authorized = self._policy.authorize_tool(name)
        except Exception:
            authorized = False
        if not authorized:
            self._record(name, "denied")
            return "Tool blocked by Yuki's local security policy.", "failed"
        handler = self._tools[name]
        supports_key = getattr(handler, "supports_idempotency_key", False) is True
        if idempotency_key is not None and supports_key:
            try:
                signature = inspect.signature(handler)
                accepts_key = ("idempotency_key" in signature.parameters or any(
                    item.kind is inspect.Parameter.VAR_KEYWORD
                    for item in signature.parameters.values()
                ))
            except (TypeError, ValueError):
                accepts_key = False
            if not accepts_key:
                self._record(name, "idempotency_contract_invalid")
                return "Tool idempotency contract is invalid.", "failed"
        try:
            if idempotency_key is not None and supports_key:
                result = str(handler(argument, idempotency_key=idempotency_key))
            else:
                result = str(handler(argument))
        except Exception:
            self._record(name, "failed")
            return "The tool failed. Its external outcome may be uncertain.", "uncertain"
        if len(result) > self._max_result_chars:
            result = result[: self._max_result_chars] + "… [truncated]"
        self._record(name, "completed")
        return result, "completed"
