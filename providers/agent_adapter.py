"""Adapt Yuki provider APIs to the message-based bounded agent loop."""
from __future__ import annotations

from typing import Any, Mapping, Sequence
import json
import inspect
import threading
import time

from providers.provider_execution import (
    ProviderCancelled, ProviderDeadlineExceeded, ProviderExecutionContext,
)


class ProviderResponseError(ValueError):
    """Provider returned an envelope outside Yuki's normalized contract."""


class ProviderAgentAdapter:
    """Present a provider's chat/tool-call API as an AgentExecutionLoop model callable.

    Providers must expose generate_chat(messages, tools, tool_choice). This
    explicit contract avoids lossy transcript flattening and keeps tool
    execution exclusively in StructuredToolProtocol.
    """

    def __init__(self, provider: Any, *, default_timeout_seconds: float = 60.0):
        if provider is None or not callable(getattr(provider, "generate_chat", None)):
            raise TypeError("provider must implement generate_chat(messages, tools, tool_choice).")
        if isinstance(default_timeout_seconds, bool) or not isinstance(default_timeout_seconds, (int, float)) or not 0 < default_timeout_seconds <= 3600:
            raise ValueError("default_timeout_seconds must be greater than 0 and at most 3600.")
        self.provider = provider
        self.default_timeout_seconds = float(default_timeout_seconds)

    @staticmethod
    def _execution_context(provider_method, timeout_seconds, cancellation_event):
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not 0 < timeout_seconds <= 3600:
            raise ValueError("timeout_seconds must be greater than 0 and at most 3600.")
        if cancellation_event is not None and not isinstance(cancellation_event, threading.Event):
            raise TypeError("cancellation_event must be a threading.Event.")
        deadline = time.monotonic() + float(timeout_seconds)
        context = ProviderExecutionContext(deadline=deadline, cancellation_event=cancellation_event or threading.Event())
        try:
            accepts_context = "execution_context" in inspect.signature(provider_method).parameters
        except (TypeError, ValueError):
            accepts_context = False
        return context, accepts_context

    @staticmethod
    def _check_execution(context):
        context.check()

    @staticmethod
    def _validate_response(response, tools, tool_choice="auto"):
        if not isinstance(response, Mapping) or set(response) - {"text", "tool_calls", "finish_reason", "usage", "request_id"}:
            raise ProviderResponseError("provider_response_envelope_invalid")
        text = response.get("text", "")
        calls = response.get("tool_calls", {"tool_calls": []})
        finish_reason = response.get("finish_reason")
        if not isinstance(text, str) or not isinstance(calls, Mapping) or set(calls) != {"tool_calls"}:
            raise ProviderResponseError("provider_response_fields_invalid")
        allowed_finish_reasons = {"stop", "length", "tool_calls", "content_filter", "function_call"}
        if finish_reason is not None and (
            not isinstance(finish_reason, str) or finish_reason not in allowed_finish_reasons
        ):
            raise ProviderResponseError("provider_finish_reason_invalid")
        call_list = calls["tool_calls"]
        if tool_choice == "none" and call_list:
            raise ProviderResponseError("provider_tool_call_not_permitted")
        if not isinstance(call_list, list) or len(call_list) > 5:
            raise ProviderResponseError("provider_tool_call_batch_invalid")
        allowed_names = {
            item.get("function", {}).get("name")
            for item in tools
            if isinstance(item, Mapping) and isinstance(item.get("function"), Mapping)
            and isinstance(item["function"].get("name"), str)
        }
        try:
            from core.structured_tool_protocol import parse_tool_calls
            normalized = parse_tool_calls(json.dumps(dict(calls), ensure_ascii=False))
        except (TypeError, ValueError) as exc:
            raise ProviderResponseError("provider_tool_call_protocol_invalid") from exc
        for call in normalized:
            if call.name not in allowed_names:
                raise ProviderResponseError("provider_tool_name_not_requested")
        normalized = {"text": text, "tool_calls": dict(calls), "finish_reason": finish_reason}
        if "usage" in response:
            usage = response["usage"]
            if (not isinstance(usage, Mapping)
                    or set(usage) != {"input_tokens", "output_tokens", "estimated"}
                    or type(usage["input_tokens"]) is not int
                    or type(usage["output_tokens"]) is not int
                    or not 0 <= usage["input_tokens"] <= 10_000_000
                    or not 0 <= usage["output_tokens"] <= 10_000_000
                    or type(usage["estimated"]) is not bool):
                raise ProviderResponseError("provider_usage_metadata_invalid")
            normalized["usage"] = dict(usage)
        if "request_id" in response:
            request_id = response["request_id"]
            if (not isinstance(request_id, str) or not request_id.strip()
                    or len(request_id) > 160):
                raise ProviderResponseError("provider_request_id_invalid")
            normalized["request_id"] = request_id
        return normalized

    @classmethod
    def _validated_stream(cls, result, context, tools, tool_choice="auto"):
        if not hasattr(result, "__iter__"):
            raise ProviderResponseError("provider_stream_not_iterable")
        iterator = iter(result)
        try:
            for event in iterator:
                cls._check_execution(context)
                if not isinstance(event, Mapping) or not isinstance(event.get("type"), str):
                    raise ProviderResponseError("provider_stream_event_invalid")
                event_type = event["type"]
                if event_type == "text_delta":
                    if set(event) != {"type", "text"} or not isinstance(event["text"], str) or not event["text"]:
                        raise ProviderResponseError("provider_stream_text_delta_invalid")
                    yield {"type": "text_delta", "text": event["text"]}
                elif event_type == "response":
                    if set(event) != {"type", "response"}:
                        raise ProviderResponseError("provider_stream_response_event_invalid")
                    yield {"type": "response", "response": cls._validate_response(event["response"], tools, tool_choice)}
                else:
                    raise ProviderResponseError("provider_stream_event_type_unsupported")
            cls._check_execution(context)
        finally:
            close = getattr(iterator, "close", None)
            if callable(close):
                close()

    def stream_model(
        self, *, messages: Sequence[Mapping[str, str]],
        tools: Sequence[dict[str, Any]], tool_choice: str = "auto",
        timeout_seconds: float | None = None,
        cancellation_event: threading.Event | None = None,
    ):
        """Expose provider streaming through the agent loop's normalized event contract."""
        stream = getattr(self.provider, "generate_chat_stream", None)
        if not callable(stream):
            raise RuntimeError("configured provider does not support native streaming.")
        # Reuse the same input contract as the non-streaming adapter.
        self.validate_inputs(messages, tools, tool_choice)
        context, accepts_context = self._execution_context(
            stream, self.default_timeout_seconds if timeout_seconds is None else timeout_seconds,
            cancellation_event,
        )
        self._check_execution(context)
        kwargs = {"messages": list(messages), "tools": list(tools), "tool_choice": tool_choice}
        if accepts_context:
            kwargs["execution_context"] = context
        result = stream(**kwargs)
        self._check_execution(context)
        return self._validated_stream(result, context, tools, tool_choice)

    @staticmethod
    def validate_inputs(messages, tools, tool_choice):
        if not isinstance(messages, (list, tuple)) or not messages or len(messages) > 128:
            raise ValueError("messages must be a non-empty sequence of at most 128 entries.")
        total_chars = 0
        for message in messages:
            if not isinstance(message, Mapping) or set(message) != {"role", "content"}:
                raise ValueError("each message must contain only role and content.")
            role, content = message["role"], message["content"]
            if role not in {"system", "user", "assistant"} or not isinstance(content, str):
                raise ValueError("message role or content is invalid.")
            total_chars += len(content)
            if total_chars > 100_000:
                raise ValueError("message transcript exceeds the character limit.")
        if not isinstance(tools, (list, tuple)) or len(tools) > 32:
            raise ValueError("tools must contain at most 32 definitions.")
        if tool_choice not in {"auto", "none", "required"}:
            raise ValueError("tool_choice is invalid.")

    def __call__(
        self, *, messages: Sequence[Mapping[str, str]],
        tools: Sequence[dict[str, Any]], tool_choice: str = "auto",
        timeout_seconds: float | None = None,
        cancellation_event: threading.Event | None = None,
        idempotency_key: str | None = None,
    ) -> Mapping[str, Any]:
        if not isinstance(messages, (list, tuple)) or not messages:
            raise ValueError("messages must be a non-empty sequence.")
        if len(messages) > 128:
            raise ValueError("message transcript exceeds the item limit.")
        normalized = []
        total_chars = 0
        for message in messages:
            if not isinstance(message, Mapping) or set(message) != {"role", "content"}:
                raise ValueError("each message must contain only role and content.")
            role, content = message["role"], message["content"]
            if not isinstance(role, str) or role not in {"system", "user", "assistant"} or not isinstance(content, str):
                raise ValueError("message role or content is invalid.")
            total_chars += len(content)
            if total_chars > 100_000:
                raise ValueError("message transcript exceeds the character limit.")
            normalized.append({"role": role, "content": content})
        if not isinstance(tools, (list, tuple)) or len(tools) > 32:
            raise ValueError("tools must contain at most 32 definitions.")
        if tool_choice not in {"auto", "none", "required"}:
            raise ValueError("tool_choice is invalid.")
        context, accepts_context = self._execution_context(
            self.provider.generate_chat,
            self.default_timeout_seconds if timeout_seconds is None else timeout_seconds,
            cancellation_event,
        )
        self._check_execution(context)
        kwargs = {"messages": normalized, "tools": list(tools), "tool_choice": tool_choice}
        if accepts_context:
            kwargs["execution_context"] = context
        if idempotency_key is not None:
            if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 128 or "\\x00" in idempotency_key:
                raise ValueError("provider_idempotency_key_invalid")
            if getattr(self.provider, "idempotency_mode", None) != "native":
                raise RuntimeError("provider_native_idempotency_not_supported")
            try:
                supports_key = "idempotency_key" in inspect.signature(self.provider.generate_chat).parameters
            except (TypeError, ValueError):
                supports_key = False
            if not supports_key:
                raise RuntimeError("provider_idempotency_contract_mismatch")
            kwargs["idempotency_key"] = idempotency_key
        response = self.provider.generate_chat(**kwargs)
        self._check_execution(context)
        return self._validate_response(response, tools, tool_choice)
