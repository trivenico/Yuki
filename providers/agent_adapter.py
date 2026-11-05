"""Adapt Yuki provider APIs to the message-based bounded agent loop."""
from __future__ import annotations

from typing import Any, Mapping, Sequence
import inspect
import threading
import time

from providers.provider_execution import (
    ProviderCancelled, ProviderDeadlineExceeded, ProviderExecutionContext,
)


class ProviderAgentAdapter:
    """Present a provider's chat/tool-call API as an AgentExecutionLoop model callable.

    Providers must expose generate_chat(messages, tools, tool_choice). This
    explicit contract avoids lossy transcript flattening and keeps tool
    execution exclusively in StructuredToolProtocol.
    """

    def __init__(self, provider: Any, *, default_timeout_seconds: float = 60.0):
        if provider is None or not callable(getattr(provider, "generate_chat", None)):
            raise TypeError("provider must implement generate_chat(messages, tools, tool_choice).")
        if isinstance(default_timeout_seconds, bool) or not isinstance(default_timeout_seconds, (int, float)) or not 0.1 <= default_timeout_seconds <= 3600:
            raise ValueError("default_timeout_seconds must be between 0.1 and 3600.")
        self.provider = provider
        self.default_timeout_seconds = float(default_timeout_seconds)

    @staticmethod
    def _execution_context(provider_method, timeout_seconds, cancellation_event):
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not 0.1 <= timeout_seconds <= 3600:
            raise ValueError("timeout_seconds must be between 0.1 and 3600.")
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
        if context.cancellation_event.is_set():
            raise ProviderCancelled("provider execution was cancelled.")
        if time.monotonic() >= context.deadline:
            raise ProviderDeadlineExceeded("provider execution deadline exceeded; outcome may be uncertain.")

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
        return result

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
        response = self.provider.generate_chat(**kwargs)
        self._check_execution(context)
        if not isinstance(response, Mapping) or set(response) - {"text", "tool_calls", "finish_reason"}:
            raise ValueError("provider returned an unsupported response envelope.")
        text = response.get("text", "")
        calls = response.get("tool_calls", {"tool_calls": []})
        if not isinstance(text, str) or not isinstance(calls, Mapping):
            raise ValueError("provider response fields are invalid.")
        return {"text": text, "tool_calls": dict(calls),
                "finish_reason": response.get("finish_reason")}
