"""Trusted composition adapter from owner approvals to exact guarded dispatch.

Only handlers explicitly registered by the application composition root are
reachable. Model-proposed skill IDs are lookup keys, never import paths.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable, Mapping

from core.guarded_tool_runtime import GuardedToolRuntime
from core.one_shot_tool_approval_policy import OneShotToolApprovalPolicy
from core.tool_action import build_tool_action
from core.tool_action_approvals import SQLiteToolApprovalStore


@dataclass(frozen=True)
class GuardedApprovalExecutionResult:
    ok: bool
    status: str
    value: str


class GuardedApprovalExecutor:
    """Issue and immediately consume a one-shot grant after owner confirmation."""

    def __init__(
        self,
        handlers: Mapping[str, Callable[[str], Any]],
        *,
        approval_store: SQLiteToolApprovalStore,
        audit_log,
        identity_validator: Callable[[str, str], bool],
        target_scopes: Mapping[str, str],
    ):
        if not isinstance(approval_store, SQLiteToolApprovalStore):
            raise TypeError("trusted SQLiteToolApprovalStore required")
        if not callable(getattr(audit_log, "append", None)):
            raise TypeError("durable approval audit log required")
        if not callable(identity_validator):
            raise TypeError("live identity validator required")
        if not isinstance(handlers, Mapping) or not handlers:
            raise ValueError("an explicit non-empty handler registry is required")
        if not isinstance(target_scopes, Mapping):
            raise TypeError("target_scopes must be a mapping")
        copied_handlers = dict(handlers)
        copied_scopes = dict(target_scopes)
        if set(copied_handlers) != set(copied_scopes):
            raise ValueError("every registered handler needs exactly one target scope")
        for name, handler in copied_handlers.items():
            if not isinstance(name, str) or not name or len(name) > 128:
                raise ValueError("handler names must be bounded non-empty strings")
            if not callable(handler):
                raise TypeError("every registered handler must be callable")
            scope = copied_scopes[name]
            if not isinstance(scope, str) or not scope.strip() or len(scope) > 512:
                raise ValueError("target scopes must be bounded non-empty strings")
        self._handlers = copied_handlers
        self._target_scopes = copied_scopes
        self._store = approval_store
        self._audit = audit_log
        self._identity_validator = identity_validator

    def __call__(self, planner, action: dict, actor: str, session_id: str):
        del planner  # The planner is intentionally not an execution authority.
        if not isinstance(action, dict):
            raise TypeError("approved action must be an object")
        skill_id = action.get("skill_id")
        arguments = action.get("arguments")
        if not isinstance(skill_id, str) or skill_id not in self._handlers:
            raise PermissionError("approved tool is not in the trusted registry")
        if not isinstance(arguments, dict):
            raise ValueError("approved tool arguments must be an object")
        if not isinstance(actor, str) or not actor or not isinstance(session_id, str) or not session_id:
            raise PermissionError("authenticated owner and session are required")

        argument_text = json.dumps(
            arguments, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        )
        handler = self._handlers[skill_id]
        envelope = build_tool_action(
            skill_id, argument_text, handler,
            owner_id=actor, session_id=session_id,
        )
        scope = self._target_scopes[skill_id]
        # This capability is created only after ApprovalCoordinator has
        # consumed the pending request, invoked the owner's confirmation
        # callback, and durably recorded the approved event.
        grant = self._store.issue(envelope, target_scope=scope)
        policy = OneShotToolApprovalPolicy(
            self._store, approval_id=grant, target_scope=scope,
            audit_log=self._audit,
        )
        runtime = GuardedToolRuntime(
            {skill_id: handler}, policy,
            owner_id=actor, session_id=session_id,
            identity_validator=self._identity_validator,
        )
        outcome = runtime.invoke_detailed(skill_id, argument_text)
        return GuardedApprovalExecutionResult(
            outcome.ok, outcome.status, outcome.value
        )
