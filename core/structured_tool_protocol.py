"""Provider-neutral structured tool-call envelopes for guarded Yuki tools.

This module does not execute model output directly. It parses a bounded JSON
proposal, validates the complete batch, then delegates each call to the existing
GuardedToolRuntime authorization and audit boundary. It intentionally does not
implement an autonomous model/tool loop or provider-specific API transport.
"""
from __future__ import annotations

import json
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
    def reject_constant(value):
        raise ValueError(f"non-standard JSON constant: {value}")

    def reject_duplicate_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON object key")
            result[key] = value
        return result

    try:
        payload = json.loads(
            response,
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicate_keys,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise ToolProtocolError("tool response must be strict JSON without duplicate keys or non-finite constants.") from exc
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
            encoded = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
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

    @property
    def registered_tool_names(self) -> frozenset[str]:
        """Names actually executable by this owner-bound guarded runtime."""
        return self._runtime.registered_tool_names

    def dispatch(self, response: str, *, execution_context: Mapping[str, str] | None = None) -> tuple[StructuredToolResult, ...]:
        # Parse the entire envelope before dispatching to avoid partial execution
        # when a later call in the same batch is malformed.
        calls = parse_tool_calls(response)
        results = []
        for call in calls:
            argument = json.dumps(call.arguments, ensure_ascii=False, separators=(",", ":"))
            output = self._runtime.invoke(call.name, argument, execution_context=execution_context)
            results.append(StructuredToolResult(call.call_id, call.name, output))
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
