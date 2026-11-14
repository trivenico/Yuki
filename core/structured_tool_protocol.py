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

_MAX_RESPONSE_CHARS = 24_000
_MAX_CALLS = 5
_MAX_ARGUMENT_BYTES = 8_000
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")


class ToolProtocolError(ValueError):
    """Raised when a provider response is not a valid structured tool envelope."""


class ToolExecutionAlreadyClaimed(RuntimeError):
    """Raised when a call ID was previously claimed and must not be replayed."""

    def __init__(self, status):
        super().__init__("tool call was already claimed; automatic replay is forbidden.")
        self.status = status


class ToolDispatchCancelled(RuntimeError):
    """Raised when cancellation stops a validated batch before its next side effect."""

    def __init__(self, completed_results):
        super().__init__("tool dispatch cancelled before the next invocation.")
        self.completed_results = tuple(completed_results)


class ToolDispatchLimitReached(RuntimeError):
    """Raised when the per-run call budget is exhausted before the next side effect."""

    def __init__(self, completed_results):
        super().__init__("tool dispatch call budget exhausted before the next invocation.")
        self.completed_results = tuple(completed_results)


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


def parse_tool_calls(response: str) -> tuple[StructuredToolCall, ...]:
    """Parse and validate every call before any tool can be invoked."""
    if not isinstance(response, str) or not response.strip():
        raise ToolProtocolError("tool response must be non-empty JSON text.")
    if len(response) > _MAX_RESPONSE_CHARS or "\x00" in response:
        raise ToolProtocolError("tool response exceeds the protocol size limit.")
    def reject_duplicate_keys(pairs):
        decoded = {}
        for key, value in pairs:
            if key in decoded:
                raise ValueError("duplicate JSON object key")
            decoded[key] = value
        return decoded

    def reject_non_finite_number(value):
        raise ValueError(f"non-finite JSON number is not permitted: {value}")

    def parse_finite_float(value):
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("JSON number is outside the finite floating-point range")
        return parsed

    try:
        payload = json.loads(
            response,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_non_finite_number,
            parse_float=parse_finite_float,
        )
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise ToolProtocolError(
            "tool response must be valid, finite JSON without duplicate object keys."
        ) from exc
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
        try:
            encoded = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
            size = len(encoded.encode("utf-8"))
        except (TypeError, ValueError, UnicodeEncodeError) as exc:
            raise ToolProtocolError("tool arguments must be JSON-compatible.") from exc
        if size > _MAX_ARGUMENT_BYTES:
            raise ToolProtocolError("tool arguments exceed the size limit.")
        seen_ids.add(call_id)
        calls.append(StructuredToolCall(call_id, name, arguments))
    return tuple(calls)


class StructuredToolProtocol:
    """Dispatch validated model proposals exclusively through guarded tools."""

    def __init__(self, runtime: GuardedToolRuntime):
        if not isinstance(runtime, GuardedToolRuntime):
            raise TypeError("runtime must be a GuardedToolRuntime.")
        self._runtime = runtime

    def dispatch(
        self, response: str, *, cancellation_requested=None,
        before_tool_call=None, after_tool_call=None, on_tool_error=None,
        max_calls: int = _MAX_CALLS,
    ) -> tuple[StructuredToolResult, ...]:
        # Parse the entire envelope before dispatching to avoid partial execution
        # when a later call in the same batch is malformed.
        if type(max_calls) is not int or not 1 <= max_calls <= 500:
            raise ValueError("max_calls must be an integer from 1 to 500.")
        if cancellation_requested is not None and not callable(cancellation_requested):
            raise TypeError("cancellation_requested must be callable.")
        if before_tool_call is not None and not callable(before_tool_call):
            raise TypeError("before_tool_call must be callable.")
        if after_tool_call is not None and not callable(after_tool_call):
            raise TypeError("after_tool_call must be callable.")
        if on_tool_error is not None and not callable(on_tool_error):
            raise TypeError("on_tool_error must be callable.")
        calls = parse_tool_calls(response)
        results = []
        for call in calls:
            if cancellation_requested is not None and cancellation_requested():
                raise ToolDispatchCancelled(results)
            if len(results) >= max_calls:
                raise ToolDispatchLimitReached(results)
            argument = json.dumps(call.arguments, ensure_ascii=False, separators=(",", ":"))
            claim = None
            if before_tool_call is not None:
                claim = before_tool_call(call, argument)
                if claim is False:
                    raise ToolExecutionAlreadyClaimed("unknown")
                if isinstance(claim, str) and claim != "claimed":
                    raise ToolExecutionAlreadyClaimed(claim)
            idempotency_key = None
            if isinstance(claim, dict):
                if claim.get("status") != "claimed" or not isinstance(claim.get("idempotency_key"), str):
                    raise ToolExecutionAlreadyClaimed(str(claim.get("status", "unknown")))
                idempotency_key = claim["idempotency_key"]
            try:
                if callable(getattr(self._runtime, "invoke_with_status", None)):
                    output, outcome = self._runtime.invoke_with_status(
                        call.name, argument, idempotency_key=idempotency_key
                    )
                else:
                    output, outcome = self._runtime.invoke(call.name, argument), "completed"
            except Exception as exc:
                if on_tool_error is not None:
                    on_tool_error(call, argument, "uncertain")
                raise
            if outcome != "completed":
                if on_tool_error is not None:
                    on_tool_error(call, argument, outcome)
                if outcome == "uncertain":
                    raise ToolExecutionAlreadyClaimed("uncertain")
            elif after_tool_call is not None:
                after_tool_call(call, argument, output)
            results.append(StructuredToolResult(call.call_id, call.name, output))
        return tuple(results)

    def supports_idempotency(self, name: str) -> bool:
        return self._runtime.supports_idempotency(name)

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
