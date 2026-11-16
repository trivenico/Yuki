"""Provider-neutral structured tool-call envelopes for guarded Yuki tools.

This module does not execute model output directly. It parses a bounded JSON
proposal, validates the complete batch, then delegates each call to the existing
GuardedToolRuntime authorization and audit boundary. It intentionally does not
implement an autonomous model/tool loop or provider-specific API transport.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any, Mapping

from core.guarded_tool_runtime import GuardedToolRuntime
from core.tool_action_context import ToolActionContext

_MAX_RESPONSE_CHARS = 24_000
_MAX_CALLS = 5
_MAX_ARGUMENT_BYTES = 8_000
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")


class ToolProtocolError(ValueError):
    """Raised when a provider response is not a valid structured tool envelope."""


@dataclass(frozen=True)
class StructuredToolCall:
    call_id: str
    name: str
    arguments: Mapping[str, Any]


@dataclass(frozen=True)
class StructuredToolResult:
    call_id: str
    name: str
    output: str


def _reject_json_constant(value: str) -> None:
    """Reject JavaScript-style numeric constants that are not valid JSON."""
    raise ValueError(f"non-standard JSON numeric constant: {value}")


def _reject_duplicate_object_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject ambiguous objects instead of silently keeping the last duplicate key."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key.")
        result[key] = value
    return result


def parse_tool_calls(response: str) -> tuple[StructuredToolCall, ...]:
    """Parse and validate every call before any tool can be invoked."""
    if not isinstance(response, str) or not response.strip():
        raise ToolProtocolError("tool response must be non-empty JSON text.")
    if len(response) > _MAX_RESPONSE_CHARS or "\x00" in response:
        raise ToolProtocolError("tool response exceeds the protocol size limit.")
    try:
        payload = json.loads(
            response,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_reject_duplicate_object_keys,
        )
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise ToolProtocolError("tool response must be valid, unambiguous JSON.") from exc
    if not isinstance(payload, dict) or set(payload) != {"tool_calls"}:
        raise ToolProtocolError("response must contain only the tool_calls field.")
    raw_calls = payload["tool_calls"]
    if not isinstance(raw_calls, list) or len(raw_calls) > _MAX_CALLS:
        raise ToolProtocolError("tool_calls must be a list of at most five calls.")

    calls = []
    seen_ids = set()
    for item in raw_calls:
        if not isinstance(item, dict) or set(item) != {"id", "name", "arguments"}:
            raise ToolProtocolError("each call must contain exactly id, name, and arguments.")
        call_id, name, arguments = item["id"], item["name"], item["arguments"]
        if not isinstance(call_id, str) or not _ID.fullmatch(call_id) or call_id in seen_ids:
            raise ToolProtocolError("tool call IDs must be unique bounded identifiers.")
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise ToolProtocolError("tool name must be a valid registered-name identifier.")
        if not isinstance(arguments, dict):
            raise ToolProtocolError("tool arguments must be a JSON object.")
        # Use the same structural, numeric and byte limits for parsed and
        # manually constructed calls so the parser cannot bypass dispatch bounds.
        _validated_call_arguments(arguments)
        seen_ids.add(call_id)
        calls.append(StructuredToolCall(call_id, name, arguments))
    return tuple(calls)


def _validated_call_arguments(arguments: Any) -> str:
    """Serialize a manually supplied call only after enforcing parser-equivalent bounds."""
    if type(arguments) is not dict:
        raise ToolProtocolError("tool arguments must be a plain JSON object.")
    pending = [(arguments, 0)]
    nodes = 0
    while pending:
        value, depth = pending.pop()
        nodes += 1
        if nodes > 4096 or depth > 32:
            raise ToolProtocolError("tool arguments exceed structural limits.")
        value_type = type(value)
        if value is None or value_type is bool or value_type is str:
            if value_type is str:
                try:
                    if len(value.encode("utf-8")) > _MAX_ARGUMENT_BYTES:
                        raise ToolProtocolError("tool arguments exceed the size limit.")
                except UnicodeEncodeError as exc:
                    raise ToolProtocolError("tool arguments contain invalid Unicode.") from exc
            continue
        if value_type is int:
            if value.bit_length() > 4096:
                raise ToolProtocolError("tool arguments contain an oversized integer.")
            continue
        if value_type is float:
            if not math.isfinite(value):
                raise ToolProtocolError("tool arguments contain a non-finite number.")
            continue
        if value_type is list:
            if len(pending) + nodes + len(value) > 4096:
                raise ToolProtocolError("tool arguments exceed structural limits.")
            pending.extend((item, depth + 1) for item in value)
            continue
        if value_type is dict:
            if len(pending) + nodes + (2 * len(value)) > 4096:
                raise ToolProtocolError("tool arguments exceed structural limits.")
            for key, item in value.items():
                if type(key) is not str:
                    raise ToolProtocolError("tool argument keys must be strings.")
                pending.append((key, depth + 1))
                pending.append((item, depth + 1))
            continue
        raise ToolProtocolError("tool arguments must contain only plain JSON values.")
    try:
        encoded = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        size = len(encoded.encode("utf-8"))
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as exc:
        raise ToolProtocolError("tool arguments must be bounded JSON-compatible data.") from exc
    if size > _MAX_ARGUMENT_BYTES:
        raise ToolProtocolError("tool arguments exceed the size limit.")
    return encoded


class StructuredToolProtocol:
    """Dispatch validated model proposals exclusively through guarded tools."""

    def __init__(self, runtime: GuardedToolRuntime):
        if not isinstance(runtime, GuardedToolRuntime):
            raise TypeError("runtime must be a GuardedToolRuntime.")
        self._runtime = runtime

    def requires_user_confirmation(self, tool_name: str) -> bool:
        """Expose only the runtime's trusted confirmation classification."""
        return self._runtime.requires_user_confirmation(tool_name)

    def dispatch_call(
        self, call: StructuredToolCall, *,
        principal_id: str | None = None,
        action_context: ToolActionContext | None = None,
        user_confirmed: bool = False,
        approval_id: str | None = None,
    ) -> StructuredToolResult:
        """Dispatch one proposal with host-supplied identity and confirmation state."""
        if not isinstance(call, StructuredToolCall):
            raise TypeError("call must be a validated StructuredToolCall.")
        if (type(call.call_id) is not str or not _ID.fullmatch(call.call_id)
                or type(call.name) is not str or not _NAME.fullmatch(call.name)):
            raise ToolProtocolError("tool call ID or name is invalid.")
        if action_context is not None and not isinstance(action_context, ToolActionContext):
            raise TypeError("action_context must be a host-created ToolActionContext.")
        if type(user_confirmed) is not bool:
            raise TypeError("user_confirmed must be a boolean.")
        argument = _validated_call_arguments(call.arguments)
        output = self._runtime.invoke(
            call.name, argument, principal_id=principal_id,
            action_context=action_context, user_confirmed=user_confirmed,
            approval_id=approval_id,
        )
        return StructuredToolResult(call.call_id, call.name, output)

    def dispatch(
        self, response: str, *,
        principal_id: str | None = None,
        action_context_factory=None,
        confirmation_check=None,
        approval_id_factory=None,
    ) -> tuple[StructuredToolResult, ...]:
        # Parse the entire envelope before dispatching to avoid partial execution
        # when a later call in the same batch is malformed.
        calls = parse_tool_calls(response)
        if action_context_factory is not None and not callable(action_context_factory):
            raise TypeError("action_context_factory must be callable.")
        if confirmation_check is not None and not callable(confirmation_check):
            raise TypeError("confirmation_check must be callable.")
        if approval_id_factory is not None and not callable(approval_id_factory):
            raise TypeError("approval_id_factory must be callable.")
        results = []
        for call in calls:
            context = (
                action_context_factory(principal_id, call.name, call.arguments)
                if action_context_factory is not None else None
            )
            requires_confirmation = self.requires_user_confirmation(call.name)
            # Match the agent loop: read-only and otherwise non-confirming tools
            # must not trigger host approval UI or approval-token issuance.
            confirmed = (
                confirmation_check(call)
                if requires_confirmation and confirmation_check is not None else False
            )
            if type(confirmed) is not bool:
                raise TypeError("confirmation_check must return a boolean.")
            # Token issuance is allowed only after a positive trusted-host decision.
            approval_id = (
                approval_id_factory(principal_id, context, call)
                if requires_confirmation and confirmed is True and context is not None
                and approval_id_factory is not None else None
            )
            results.append(self.dispatch_call(
                call, principal_id=principal_id, action_context=context,
                user_confirmed=confirmed, approval_id=approval_id,
            ))
        return tuple(results)

    @staticmethod
    def format_results(results: tuple[StructuredToolResult, ...]) -> str:
        """Serialize bounded tool outputs as untrusted evidence for the next model turn."""
        if not isinstance(results, tuple) or len(results) > _MAX_CALLS:
            raise ValueError("results must be a bounded tuple of structured tool results.")
        payload = {
            "tool_results": [
                {"id": item.call_id, "name": item.name, "output": item.output,
                 "trust": "untrusted_tool_output"}
                for item in results
            ]
        }
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > _MAX_RESPONSE_CHARS:
            raise ValueError("formatted tool results exceed the protocol size limit.")
        return encoded
