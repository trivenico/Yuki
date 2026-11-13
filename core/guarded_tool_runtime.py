"""Guarded dispatch for explicitly registered CLI tools."""
from __future__ import annotations

from dataclasses import dataclass
import json
from types import MappingProxyType
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class ApprovalRequired:
    """A policy decision that requires owner approval instead of execution."""

    action: Mapping[str, Any]


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
        approval_request: Callable[[str, dict[str, Any], Mapping[str, str]], str] | None = None,
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
        if approval_request is not None and not callable(approval_request):
            raise TypeError("approval_request must be callable when provided")
        if approval_request is not None and not require_audit:
            raise ValueError("approval requests require durable audit mode")
        self._tools = dict(tools)
        self._policy = policy
        self._audit = audit
        self._require_audit = require_audit
        self._approval_request = approval_request
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
    def approval_request_callback(self):
        """Return the host approval-request adapter, if one is configured."""
        return self._approval_request

    def with_approval_request(self, callback: Callable[[str, dict[str, Any], Mapping[str, str]], str]):
        """Return an equivalent runtime with a durable host approval adapter attached.

        This is an immutable composition operation: an existing, different
        adapter cannot be silently replaced, and approval remains unavailable
        unless required audit mode is enabled.
        """
        if not callable(callback):
            raise TypeError("approval request adapter must be callable")
        if not self._require_audit:
            raise ValueError("approval requests require durable audit mode")
        if self._approval_request is not None:
            if self._approval_request is callback:
                return self
            raise ValueError("guarded runtime already has a different approval adapter")
        return GuardedToolRuntime(
            self._tools,
            self._policy,
            audit=self._audit,
            max_argument_chars=self._max_argument_chars,
            max_result_chars=self._max_result_chars,
            require_audit=True,
            approval_request=callback,
        )

    @property
    def registered_tool_names(self) -> frozenset[str]:
        """Return the immutable set of handlers executable by this runtime."""
        return frozenset(self._tools)

    @property
    def registered_tool_handlers(self) -> tuple[Callable[[str], Any], ...]:
        """Return handler identities for host-side isolation validation."""
        return tuple(self._tools.values())

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

    def execute_approved_action(
        self, action: Mapping[str, Any], *, execution_context: Mapping[str, str]
    ) -> str:
        """Execute an immutable approved snapshot through this exact guarded registry.

        Approval is not authorization: the policy must independently map the
        registered tool to the supplied skill and return literal True from its
        approved-action authorization hook. No model-supplied executor or
        handler name can bypass the registry.
        """
        required_context = {"actor", "session_id", "conversation_id", "request_id"}
        if (
            not isinstance(execution_context, Mapping)
            or set(execution_context) != required_context
            or any(not isinstance(v, str) or not v.strip() or len(v) > 256
                   for v in execution_context.values())
        ):
            raise PermissionError("approved execution requires authenticated request context")
        context = MappingProxyType(dict(execution_context))
        if not isinstance(action, Mapping) or set(action) != {
            "tool_name", "skill_id", "arguments", "execution_context"
        }:
            raise ValueError("approved action snapshot has an invalid shape")
        tool_name = action.get("tool_name")
        skill_id = action.get("skill_id")
        arguments = action.get("arguments")
        stored_context = action.get("execution_context")
        if (
            not isinstance(tool_name, str) or not tool_name or len(tool_name) > 128
            or not isinstance(skill_id, str) or not skill_id.strip() or len(skill_id) > 120
            or not isinstance(arguments, Mapping)
            or not isinstance(stored_context, Mapping)
            or dict(stored_context) != dict(context)
        ):
            raise ValueError("approved action does not match its authenticated request")
        if tool_name not in self._tools:
            self._record(tool_name if isinstance(tool_name, str) else "invalid", "approved_tool_not_found")
            raise PermissionError("approved tool is not registered in this owner runtime")
        try:
            snapshot = json.loads(json.dumps(
                dict(arguments), ensure_ascii=False, allow_nan=False,
                sort_keys=True, separators=(",", ":"),
            ))
            encoded = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            self._record(tool_name, "approved_arguments_invalid")
            raise ValueError("approved arguments must be JSON-safe") from exc
        if len(encoded) > self._max_argument_chars:
            self._record(tool_name, "approved_arguments_too_large")
            raise ValueError("approved arguments exceed the guarded runtime limit")
        skill_resolver = getattr(self._policy, "skill_id_for_tool", None)
        approved_authorizer = getattr(self._policy, "authorize_approved_tool_context", None)
        if not callable(skill_resolver) or not callable(approved_authorizer):
            self._record(tool_name, "approved_execution_policy_unavailable")
            raise PermissionError("approved execution policy is not configured")
        try:
            trusted_skill = skill_resolver(tool_name)
            authorized = approved_authorizer(tool_name, skill_id, context)
        except Exception:
            trusted_skill, authorized = None, False
        if trusted_skill != skill_id or authorized is not True:
            self._record(tool_name, "approved_execution_denied")
            raise PermissionError("approved action failed independent owner policy validation")
        if not self._record(tool_name, "approved_execution_started"):
            raise RuntimeError("approved execution start audit failed")
        try:
            result = str(self._tools[tool_name](encoded))
        except Exception as exc:
            self._record(tool_name, "approved_execution_outcome_unknown")
            raise RuntimeError("approved tool execution outcome is unknown") from exc
        if len(result) > self._max_result_chars:
            result = result[:self._max_result_chars] + "… [truncated]"
        if not self._record(tool_name, "approved_execution_completed"):
            raise RuntimeError("approved tool may have completed but completion audit failed")
        return result

    def invoke(self, name: str, argument: str = "", *, execution_context: Mapping[str, str] | None = None) -> str:
        if execution_context is not None:
            required = {"actor", "session_id", "conversation_id", "request_id"}
            if (not isinstance(execution_context, Mapping)
                    or set(execution_context) != required
                    or any(not isinstance(value, str) or not value.strip() or len(value) > 256
                           for value in execution_context.values())):
                self._record(name if isinstance(name, str) and name else "invalid", "invalid_execution_context")
                return "Tool blocked because its authenticated execution context is invalid."
            # Policies receive an immutable host-created snapshot, never model data.
            execution_context = MappingProxyType(dict(execution_context))
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
        contextual_authorizer = getattr(self._policy, "authorize_tool_context", None)
        # Production runtimes require durable audit and authenticated identity.
        # Do not let a legacy name-only policy turn missing request identity into
        # an executable operation merely because it lacks the contextual hook.
        if (callable(contextual_authorizer) or self._require_audit) and execution_context is None:
            recorded = self._record(name, "missing_authenticated_execution_context")
            if self._require_audit and not recorded:
                return "Tool blocked because its required audit record could not be written."
            return "Tool blocked because authenticated execution context is required."
        try:
            if callable(contextual_authorizer):
                authorized = contextual_authorizer(name, execution_context)
            else:
                # Legacy policies remain supported only when they do not declare
                # the stronger authenticated-context authorization contract.
                authorized = self._policy.authorize_tool(name)
        except Exception:
            authorized = False
        # Approval is a separate decision type, never a truthy authorization.
        # It snapshots a bounded JSON action and records a pending request; the
        # registered handler is deliberately not invoked on this path.
        if isinstance(authorized, ApprovalRequired):
            if self._approval_request is None or execution_context is None:
                self._record(name, "approval_unavailable")
                return "Tool blocked because owner approval is not configured."
            try:
                snapshot = json.loads(json.dumps(
                    dict(authorized.action), ensure_ascii=False, allow_nan=False,
                    sort_keys=True, separators=(",", ":"),
                ))
                if not isinstance(snapshot, dict) or len(json.dumps(
                    snapshot, ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8")) > 16 * 1024:
                    raise ValueError("approval action must be an object no larger than 16 KiB")
            except (TypeError, ValueError):
                self._record(name, "invalid_approval_action")
                return "Tool blocked because its approval action is invalid."
            if not self._record(name, "approval_requested"):
                return "Tool blocked because its required approval audit record could not be written."
            try:
                pending_id = self._approval_request(name, snapshot, execution_context)
            except Exception:
                self._record(name, "approval_request_failed")
                return "Tool blocked because an approval request could not be created."
            if not isinstance(pending_id, str) or not pending_id.strip() or len(pending_id) > 256:
                self._record(name, "approval_request_invalid_result")
                return "Tool blocked because the approval service returned an invalid request."
            return json.dumps({"status": "pending_approval", "pending_id": pending_id}, separators=(",", ":"))
        # Authorization is a closed boolean contract. Truthy values such as
        # "false", 1, or custom objects must never grant executable authority.
        if authorized is not True:
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
