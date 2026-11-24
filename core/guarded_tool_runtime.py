"""Guarded dispatch for explicitly registered CLI tools."""
from __future__ import annotations

from typing import Any, Callable

from core.authorization import (
    AuthorizationPolicy,
    AuthorizationRequest,
    Principal,
    evaluate_authorization,
)


class GuardedToolRuntime:
    def __init__(
        self,
        tools: dict[str, Callable[[str], Any]],
        policy: Any,
        *,
        principal: Principal | None = None,
        policy_revision: str | None = None,
        task_id: str | None = None,
        delegation_id: str | None = None,
        approval_id: str | None = None,
        risk: str = "medium",
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
        self._typed_policy = callable(getattr(policy, "authorize", None))
        if self._typed_policy:
            if not isinstance(policy, AuthorizationPolicy):
                raise TypeError("typed policy must implement AuthorizationPolicy")
            if not isinstance(principal, Principal):
                raise TypeError("typed authorization requires an authenticated Principal")
        elif not callable(getattr(policy, "authorize_tool", None)):
            raise TypeError("policy must provide authorize_tool(name) or authorize(request)")
        if audit is not None and not callable(audit):
            raise TypeError("audit must be callable when provided")
        self._tools = dict(tools)
        self._policy = policy
        self._principal = principal
        self._policy_revision = policy_revision
        self._task_id = task_id
        self._delegation_id = delegation_id
        self._approval_id = approval_id
        self._risk = risk
        if self._typed_policy:
            # Validate all request metadata at construction, before any invocation.
            AuthorizationRequest(
                principal=principal,
                action="tool:invoke",
                resource="tool:configuration",
                risk=risk,
                task_id=task_id,
                delegation_id=delegation_id,
                approval_id=approval_id,
                expected_policy_revision=policy_revision,
            )
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

    def _authorized(self, name: str) -> bool:
        try:
            if self._typed_policy:
                request = AuthorizationRequest(
                    principal=self._principal,
                    action="tool:invoke",
                    resource="tool:" + name,
                    risk=self._risk,
                    task_id=self._task_id,
                    delegation_id=self._delegation_id,
                    approval_id=self._approval_id,
                    expected_policy_revision=self._policy_revision,
                )
                decision = evaluate_authorization(self._policy, request)
                return decision.allowed is True
            # Compatibility path for existing integrations. New integrations should
            # use AuthorizationPolicy with an authenticated Principal.
            return self._policy.authorize_tool(name) is True
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
        if not self._authorized(name):
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
