"""Typed, fail-closed tool execution for Yuki agents.

Contracts validate untrusted arguments; policy authorizes each invocation; exact-call
approval gates side effects. This module is an in-process dispatcher, not a sandbox.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Mapping

from core.task_contracts import (
    ApprovalDecision,
    RiskLevel,
    ToolCall,
    ToolDefinition,
    ToolResult,
    validate_tool_call,
)


class TypedToolRuntime:
    """Dispatch registered handlers only after schema, policy, and approval checks."""

    def __init__(
        self,
        tools: Mapping[str, tuple[ToolDefinition, Callable[[Mapping[str, Any]], Any]]],
        policy: Any,
        *,
        audit: Callable[[str, str], None] | None = None,
        max_output_chars: int = 16_000,
    ) -> None:
        if not isinstance(tools, Mapping):
            raise TypeError("tools must be a mapping")
        if not callable(getattr(policy, "authorize_tool", None)):
            raise TypeError("policy must provide authorize_tool(name)")
        if audit is not None and not callable(audit):
            raise TypeError("audit must be callable")
        if isinstance(max_output_chars, bool) or not isinstance(max_output_chars, int) or not 1 <= max_output_chars <= 16_000:
            raise ValueError("max_output_chars must be between 1 and 16000")
        registry = {}
        for name, entry in tools.items():
            if not isinstance(name, str) or not isinstance(entry, tuple) or len(entry) != 2:
                raise ValueError("each tool must map a name to (ToolDefinition, handler)")
            definition, handler = entry
            if not isinstance(definition, ToolDefinition) or definition.name != name:
                raise ValueError("registry key must match its ToolDefinition name")
            if not callable(handler):
                raise TypeError("tool handler must be callable")
            if name in registry:
                raise ValueError("duplicate tool registration")
            registry[name] = (definition, handler)
        self._tools = registry
        self._policy = policy
        self._audit = audit
        self._max_output_chars = max_output_chars

    def _record(self, name: str, outcome: str) -> None:
        if self._audit is not None:
            try:
                self._audit("tool:" + name[:64], outcome)
            except Exception:
                # Audit transport outages never grant permissions or leak tool data.
                pass

    def invoke(
        self,
        call: ToolCall,
        *,
        approval: ApprovalDecision | None = None,
    ) -> ToolResult:
        if not isinstance(call, ToolCall):
            raise TypeError("invoke requires a ToolCall")
        registered = self._tools.get(call.tool_name)
        if registered is None:
            self._record(call.tool_name, "not_found")
            return ToolResult(call.call_id, False, error_code="tool_not_found")
        definition, handler = registered
        try:
            validate_tool_call(call, definition)
        except (TypeError, ValueError):
            self._record(call.tool_name, "invalid_arguments")
            return ToolResult(call.call_id, False, error_code="invalid_arguments")

        try:
            allowed = self._policy.authorize_tool(call.tool_name)
        except Exception:
            allowed = False
        if allowed is not True:
            self._record(call.tool_name, "denied")
            return ToolResult(call.call_id, False, error_code="permission_denied")

        requires_approval = definition.side_effecting or definition.risk is RiskLevel.HIGH
        if requires_approval:
            if not isinstance(approval, ApprovalDecision) or not approval.matches(call) or not approval.approved:
                self._record(call.tool_name, "approval_required")
                return ToolResult(call.call_id, False, error_code="approval_required")

        try:
            raw = handler(call.arguments)
            if isinstance(raw, str):
                output = raw
            else:
                output = json.dumps(raw, ensure_ascii=True, allow_nan=False, sort_keys=True)
            if len(output) > self._max_output_chars:
                marker = "… [truncated]"
                if self._max_output_chars <= len(marker):
                    output = marker[:self._max_output_chars]
                else:
                    output = output[:self._max_output_chars - len(marker)] + marker
        except Exception:
            self._record(call.tool_name, "failed")
            return ToolResult(call.call_id, False, error_code="tool_execution_failed")

        self._record(call.tool_name, "completed")
        return ToolResult(call.call_id, True, output=output)
