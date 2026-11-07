"""Privacy-safe, versioned metadata for model invocations.

The recorder stores operational metadata only. It never stores prompts,
responses, tool arguments, credentials, or retrieved memory content.
"""
from __future__ import annotations

import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from providers.agent_adapter import ProviderAgentAdapter

_SCHEMA_VERSION = 1
_PROVIDER_ID = re.compile(r"^[a-z][a-z0-9_.-]{0,47}$")


class InvocationRecordingProviderAdapter(ProviderAgentAdapter):
    """Provider adapter that emits bounded, content-free invocation records."""

    def __init__(
        self, provider: Any, *, provider_id: str, model_id: str,
        record_sink: Callable[[dict[str, Any]], None],
        clock: Callable[[], float] = time.monotonic,
        utc_now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        invocation_id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
    ):
        super().__init__(provider)
        if not isinstance(provider_id, str) or not _PROVIDER_ID.fullmatch(provider_id):
            raise ValueError("invalid_provider_id")
        if not isinstance(model_id, str) or not model_id.strip() or len(model_id) > 160:
            raise ValueError("invalid_model_id")
        if not callable(record_sink) or not callable(clock) or not callable(utc_now):
            raise TypeError("invocation_record_callbacks_must_be_callable")
        if not callable(invocation_id_factory):
            raise TypeError("invocation_id_factory_must_be_callable")
        self.provider_id = provider_id
        self.model_id = model_id.strip()
        self.record_sink = record_sink
        self.clock = clock
        self.utc_now = utc_now
        self.invocation_id_factory = invocation_id_factory

    @staticmethod
    def _counts(messages: Sequence[Mapping[str, str]], tools: Sequence[dict[str, Any]]):
        return len(messages), sum(len(item["content"]) for item in messages), len(tools)

    def _record(self, *, started, started_at, message_count, input_chars,
                tool_count, outcome, response_chars=0, tool_call_count=0,
                error_type=None):
        record = {
            "schema_version": _SCHEMA_VERSION,
            "invocation_id": self.invocation_id_factory(),
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "started_at": started_at,
            "duration_ms": round(max(0.0, (self.clock() - started) * 1000.0), 3),
            "outcome": outcome,
            "message_count": message_count,
            "input_chars": input_chars,
            "tool_count": tool_count,
            "response_chars": response_chars,
            "tool_call_count": tool_call_count,
            "error_type": error_type,
        }
        self.record_sink(record)

    def __call__(self, *, messages, tools, tool_choice="auto"):
        self.validate_inputs(messages, tools, tool_choice)
        message_count, input_chars, tool_count = self._counts(messages, tools)
        started = self.clock()
        started_at = self.utc_now().astimezone(timezone.utc).isoformat()
        try:
            result = super().__call__(messages=messages, tools=tools, tool_choice=tool_choice)
        except Exception as exc:
            self._record(
                started=started, started_at=started_at, message_count=message_count,
                input_chars=input_chars, tool_count=tool_count, outcome="error",
                error_type=type(exc).__name__,
            )
            raise
        calls = result["tool_calls"].get("tool_calls", [])
        self._record(
            started=started, started_at=started_at, message_count=message_count,
            input_chars=input_chars, tool_count=tool_count, outcome="success",
            response_chars=len(result["text"]), tool_call_count=len(calls),
        )
        return result

    def stream_model(self, *, messages, tools, tool_choice="auto"):
        self.validate_inputs(messages, tools, tool_choice)
        message_count, input_chars, tool_count = self._counts(messages, tools)
        started = self.clock()
        started_at = self.utc_now().astimezone(timezone.utc).isoformat()
        source = super().stream_model(messages=messages, tools=tools, tool_choice=tool_choice)

        def observed():
            response = None
            response_chars = 0
            try:
                for event in source:
                    if event.get("type") == "text_delta":
                        response_chars += len(event.get("text", ""))
                    elif event.get("type") == "response":
                        response = event.get("response")
                    yield event
                if not isinstance(response, Mapping):
                    raise RuntimeError("provider_stream_missing_final_response")
                calls = response.get("tool_calls", {}).get("tool_calls", [])
                self._record(
                    started=started, started_at=started_at, message_count=message_count,
                    input_chars=input_chars, tool_count=tool_count, outcome="success",
                    response_chars=response_chars, tool_call_count=len(calls),
                )
            except GeneratorExit:
                self._record(
                    started=started, started_at=started_at, message_count=message_count,
                    input_chars=input_chars, tool_count=tool_count, outcome="error",
                    response_chars=response_chars, error_type="StreamClosedEarly",
                )
                raise
            except Exception as exc:
                self._record(
                    started=started, started_at=started_at, message_count=message_count,
                    input_chars=input_chars, tool_count=tool_count, outcome="error",
                    response_chars=response_chars, error_type=type(exc).__name__,
                )
                raise
        return observed()

 
def create_invocation_trace_decorator(
    *, record_sink: Callable[[dict[str, Any]], None],
    provider_id_for_owner: Callable[[str], str],
    model_id_for_owner: Callable[[str], str],
) -> Callable[[str, ProviderAgentAdapter], ProviderAgentAdapter]:
    """Build an owner-scoped runtime decorator backed by a durable record sink."""
    if not callable(record_sink) or not callable(provider_id_for_owner) or not callable(model_id_for_owner):
        raise TypeError("invocation_trace_decorator_callbacks_must_be_callable")

    def decorate(owner_id: str, adapter: ProviderAgentAdapter) -> ProviderAgentAdapter:
        if not isinstance(owner_id, str) or not owner_id.strip():
            raise ValueError("invalid_authenticated_owner_id")
        if not isinstance(adapter, ProviderAgentAdapter):
            raise TypeError("provider_adapter_must_be_ProviderAgentAdapter")
        return InvocationRecordingProviderAdapter(
            adapter.provider,
            provider_id=provider_id_for_owner(owner_id),
            model_id=model_id_for_owner(owner_id),
            record_sink=record_sink,
        )

    return decorate
