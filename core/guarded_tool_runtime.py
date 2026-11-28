"""Guarded dispatch for explicitly registered CLI tools."""
from __future__ import annotations

from typing import Any, Callable

from core.tool_action import ToolActionEnvelope, build_tool_action


class GuardedToolRuntime:
    def __init__(
        self,
        tools: dict[str, Callable[[str], Any]],
        policy: Any,
        *,
        audit: Callable[[str, str], None] | None = None,
        max_argument_chars: int = 20_000,
        max_result_chars: int = 4_000,
        owner_id: str | None = None,
        session_id: str | None = None,
        identity_validator: Callable[[str, str], bool] | None = None,
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
        if not callable(getattr(policy, "authorize_action", None)):
            raise TypeError("policy must provide authorize_action(ToolActionEnvelope)")
        for value, label in ((owner_id, "owner_id"), (session_id, "session_id")):
            if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 256):
                raise ValueError(f"{label} must be non-empty bounded text when provided")
        if audit is not None and not callable(audit):
            raise TypeError("audit must be callable when provided")
        if identity_validator is not None and not callable(identity_validator):
            raise TypeError("identity_validator must be callable when provided")
        if tools and identity_validator is None:
            raise ValueError("non-empty tool registries require live identity validation")
        if tools and (owner_id is None or session_id is None):
            raise ValueError("non-empty tool registries require owner_id and session_id")
        self._tools = dict(tools)
        self._implementations = {}
        for name, handler in self._tools.items():
            # Fingerprint at registration and again before invocation to detect
            # replacement or mutation of the registered Python implementation.
            from core.tool_action import implementation_identity
            self._implementations[name] = implementation_identity(handler)
        self._policy = policy
        self._audit = audit
        self._max_argument_chars = max_argument_chars
        self._max_result_chars = max_result_chars
        self._owner_id = owner_id
        self._session_id = session_id
        self._identity_validator = identity_validator

    def _identity_is_live(self) -> bool:
        if self._identity_validator is None or not self._owner_id or not self._session_id:
            return False
        try:
            return self._identity_validator(self._owner_id, self._session_id) is True
        except Exception:
            return False

    def _record(self, name: str, outcome: str) -> None:
        if self._audit is not None:
            try:
                self._audit("tool:" + name[:128], outcome)
            except Exception:
                # This callback is best-effort telemetry only. Policies must
                # enforce their own durable audit-before-execution requirement.
                pass

    def invoke(self, name: str, argument: str = "") -> str:
        if not isinstance(name, str) or not name or len(name) > 128:
            self._record("invalid", "invalid_name")
            return "Invalid tool name."
        if not isinstance(argument, str) or len(argument) > self._max_argument_chars:
            self._record(name, "invalid_argument")
            return "Tool argument is invalid or too long."
        handler = self._tools.get(name)
        if handler is None:
            self._record(name, "not_found")
            return "Unknown tool."
        try:
            from core.tool_action import implementation_identity
            current_identity = implementation_identity(handler)
            if current_identity != self._implementations[name]:
                raise ValueError("registered tool implementation changed")
            if not self._identity_is_live():
                raise PermissionError("owner session is not live")
            action = build_tool_action(
                name, argument, handler,
                owner_id=self._owner_id, session_id=self._session_id,
            )
            authorized = self._policy.authorize_action(action) is True
        except Exception:
            authorized = False
        if not authorized:
            self._record(name, "denied")
            return "Tool blocked by Yuki's local security policy."
        # Revalidate after policy/grant consumption and immediately before the
        # side effect. Revocation during authorization therefore fails closed.
        if not self._identity_is_live():
            self._record(name, "identity_revoked")
            return "Tool blocked by Yuki's local security policy."
        try:
            result = str(handler(argument))
        except Exception:
            self._record(name, "failed")
            return "The tool failed. Check the input and try again."
        if len(result) > self._max_result_chars:
            result = result[: self._max_result_chars] + "… [truncated]"
        self._record(name, "completed")
        return result
