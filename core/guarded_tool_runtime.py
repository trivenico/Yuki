"""Guarded dispatch for explicitly registered CLI tools."""
from __future__ import annotations

import json
from typing import Any, Callable

from core.tool_action_context import ToolActionContext, ToolActionContextError
from core.tool_action_approvals import SQLiteToolActionApprovalStore, ToolActionApprovalError
from core.tool_action_journal import ToolActionJournal, ToolActionJournalError
from core.tool_capabilities import ToolCapabilityCatalog, ToolCapabilityError, ToolEffect


def _strict_json_object(argument: str) -> dict[str, Any]:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON object key.")
            result[key] = value
        return result

    def reject_non_finite(value: str) -> None:
        raise ValueError("non-finite JSON number.")

    value = json.loads(
        argument, object_pairs_hook=reject_duplicate_keys, parse_constant=reject_non_finite,
    )
    if not isinstance(value, dict):
        raise ValueError("tool arguments must be a JSON object.")
    return value


class GuardedToolRuntime:
    def __init__(
        self,
        tools: dict[str, Callable[[str], Any]],
        policy: Any,
        *,
        audit: Callable[[str, str], None] | None = None,
        max_argument_chars: int = 20_000,
        max_result_chars: int = 4_000,
        capabilities: ToolCapabilityCatalog | None = None,
        require_action_context: bool = False,
        action_journal: ToolActionJournal | None = None,
        approval_store: SQLiteToolActionApprovalStore | None = None,
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
        if type(require_action_context) is not bool:
            raise TypeError("require_action_context must be a boolean")
        if require_action_context and capabilities is None:
            raise ValueError("action-context enforcement requires a capability catalog")
        if require_action_context and action_journal is None:
            raise ValueError("action-context enforcement requires a durable action journal")
        if action_journal is not None and not isinstance(action_journal, ToolActionJournal):
            raise TypeError("action_journal must be a ToolActionJournal")
        if approval_store is not None and not isinstance(approval_store, SQLiteToolActionApprovalStore):
            raise TypeError("approval_store must be a SQLiteToolActionApprovalStore")
        if capabilities is not None:
            if not isinstance(capabilities, ToolCapabilityCatalog):
                raise TypeError("capabilities must be a ToolCapabilityCatalog")
            if set(capabilities.names()) != set(tools):
                raise ValueError("capability catalog must classify every registered tool exactly once")
        self._tools = dict(tools)
        self._capabilities = capabilities
        self._require_action_context = require_action_context
        self._action_journal = action_journal
        self._approval_store = approval_store
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

    def invoke(
        self, name: str, argument: str = "", *,
        action_context: ToolActionContext | None = None,
        principal_id: str | None = None,
        user_confirmed: bool = False,
        approval_id: str | None = None,
    ) -> str:
        if not isinstance(name, str) or not name or len(name) > 128:
            self._record("invalid", "invalid_name")
            return "Invalid tool name."
        if not isinstance(argument, str) or len(argument) > self._max_argument_chars:
            self._record(name, "invalid_argument")
            return "Tool argument is invalid or too long."
        if name not in self._tools:
            self._record(name, "not_found")
            return "Unknown tool."

        capability = None
        action_request = None
        if self._capabilities is not None:
            try:
                capability = self._capabilities.resolve(name)
            except ToolCapabilityError:
                self._record(name, "capability_denied")
                return "Tool blocked: capability metadata is missing."
            if capability.effect is ToolEffect.SIDE_EFFECTING and self._require_action_context:
                if not isinstance(action_context, ToolActionContext) or principal_id is None:
                    self._record(name, "action_context_missing")
                    return "Tool blocked: trusted action context is required."
                try:
                    action_request = _strict_json_object(argument)
                    action_context.verify(principal_id, name, action_request)
                except (TypeError, ValueError, ToolActionContextError):
                    self._record(name, "action_context_mismatch")
                    return "Tool blocked: trusted action context does not match the request."
                if capability.requires_user_confirmation:
                    if self._approval_store is None or not isinstance(approval_id, str):
                        self._record(name, "confirmation_required")
                        return "Tool blocked: a durable, action-bound approval is required."
                    try:
                        approved = self._approval_store.consume(
                            approval_id, action_context, principal_id=principal_id,
                        )
                    except (ToolActionApprovalError, TypeError, ValueError):
                        approved = False
                    if not approved:
                        self._record(name, "approval_invalid_or_consumed")
                        return "Tool blocked: approval is invalid, expired, or already used."

        try:
            authorized = self._policy.authorize_tool(name)
        except Exception:
            authorized = False
        if not authorized:
            self._record(name, "denied")
            return "Tool blocked by Yuki's local security policy."

        lease_owner = None
        if (
            capability is not None
            and capability.effect is ToolEffect.SIDE_EFFECTING
            and self._require_action_context
        ):
            try:
                reserved = self._action_journal.reserve(
                    action_context.idempotency_key, name, action_request,
                    action_id=action_context.action_id,
                )
                action = self._action_journal.begin_execution(
                    action_context.idempotency_key, reserved.request_digest,
                )
                if action.state == "succeeded":
                    self._record(name, "idempotent_replay")
                    return str(action.result)
                lease_owner = action.lease_owner
                if not lease_owner:
                    self._record(name, "journal_invalid_lease")
                    return "Tool blocked: durable execution lease is unavailable."
            except (ToolActionJournalError, ValueError, TypeError):
                self._record(name, "journal_blocked")
                return "Tool blocked: action is already running or requires reconciliation."

        try:
            result = str(self._tools[name](argument))
        except Exception:
            if lease_owner is not None:
                try:
                    self._action_journal.mark_outcome_unknown(
                        action_context.idempotency_key, lease_owner, "handler_exception",
                    )
                except ToolActionJournalError:
                    self._record(name, "journal_reconciliation_required")
            self._record(name, "failed")
            if lease_owner is not None:
                return "The tool failed. Its external outcome may require reconciliation."
            return "The tool failed. Check the input and try again."
        if len(result) > self._max_result_chars:
            result = result[: self._max_result_chars] + "… [truncated]"
        if lease_owner is not None:
            try:
                self._action_journal.mark_succeeded(
                    action_context.idempotency_key, lease_owner, result,
                )
            except ToolActionJournalError:
                self._record(name, "journal_reconciliation_required")
                return "The tool may have completed, but its result was not committed. Reconciliation is required."
        self._record(name, "completed")
        return result
