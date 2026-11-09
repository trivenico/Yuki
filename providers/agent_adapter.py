"""Adapt Yuki provider APIs to the message-based bounded agent loop."""
from __future__ import annotations

import json
from core.token_usage import provider_call_lock
from typing import Any, Callable, Mapping, Sequence


class ProviderAgentAdapter:
    """Present a provider's chat/tool-call API as an AgentExecutionLoop model callable.

    Providers must expose generate_chat(messages, tools, tool_choice). This
    explicit contract avoids lossy transcript flattening and keeps tool
    execution exclusively in StructuredToolProtocol.
    """

    def __init__(self, provider: Any, *, usage_tracker=None,
                 on_usage_error: Callable[[Exception], None] | None = None):
        if provider is None or not callable(getattr(provider, "generate_chat", None)):
            raise TypeError("provider must implement generate_chat(messages, tools, tool_choice).")
        if usage_tracker is not None and not callable(getattr(usage_tracker, "record", None)):
            raise TypeError("usage_tracker must expose record().")
        if on_usage_error is not None and not callable(on_usage_error):
            raise TypeError("on_usage_error must be callable.")
        self.provider = provider
        self.usage_tracker = usage_tracker
        self.on_usage_error = on_usage_error
        self.last_usage_error = None

    def _record_usage(self, messages, tools, response):
        """Record exact provider counts or explicitly marked character-based estimates.

        Metering is best-effort telemetry: serialization or storage failures are
        observable through last_usage_error/callback but never fail inference.
        """
        if self.usage_tracker is None:
            return
        try:
            usage = response.get("usage") if isinstance(response, Mapping) else None
            exact = (
                isinstance(usage, Mapping)
                and type(usage.get("input_tokens")) is int and usage["input_tokens"] >= 0
                and type(usage.get("output_tokens")) is int and usage["output_tokens"] >= 0
                and type(usage.get("estimated")) is bool
            )
            if exact:
                input_tokens = usage["input_tokens"]
                output_tokens = usage["output_tokens"]
                estimated = usage["estimated"]
            else:
                input_chars = sum(len(item["content"]) for item in messages)
                input_chars += len(json.dumps(list(tools), ensure_ascii=False, separators=(",", ":")))
                output_text = response.get("text", "") if isinstance(response, Mapping) else ""
                output_calls = response.get("tool_calls", {}) if isinstance(response, Mapping) else {}
                output_chars = len(output_text) + len(json.dumps(output_calls, ensure_ascii=False, separators=(",", ":")))
                input_tokens = (input_chars + 3) // 4
                output_tokens = (output_chars + 3) // 4
                estimated = True
            self.usage_tracker.record(
                provider=self.provider.__class__.__name__.lower(),
                model=str(getattr(self.provider, "model", "")),
                input_tokens=input_tokens, output_tokens=output_tokens,
                estimated=estimated,
            )
            self.last_usage_error = None
        except Exception as exc:
            self.last_usage_error = type(exc).__name__
            if self.on_usage_error is not None:
                try:
                    self.on_usage_error(exc)
                except Exception:
                    pass

    def stream_model(
        self, *, messages: Sequence[Mapping[str, str]],
        tools: Sequence[dict[str, Any]], tool_choice: str = "auto",
    ):
        """Expose provider streaming through the agent loop's normalized event contract."""
        stream = getattr(self.provider, "generate_chat_stream", None)
        if not callable(stream):
            raise RuntimeError("configured provider does not support native streaming.")
        # Reuse the same input contract as the non-streaming adapter.
        self._validate_inputs(messages, tools, tool_choice)
        normalized_messages = [{"role": item["role"], "content": item["content"]} for item in messages]
        def tracked_stream():
            with provider_call_lock(self.provider):
                final_response = None
                for event in stream(messages=list(messages), tools=list(tools), tool_choice=tool_choice):
                    if isinstance(event, Mapping) and event.get("type") == "response":
                        response = event.get("response")
                        if isinstance(response, Mapping):
                            final_response = dict(response)
                            safe_response = {key: value for key, value in response.items() if key != "usage"}
                            yield {"type": "response", "response": safe_response}
                            continue
                    yield event
                if final_response is not None:
                    self._record_usage(normalized_messages, tools, final_response)
        return tracked_stream()

    @staticmethod
    def _validate_inputs(messages, tools, tool_choice):
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
        with provider_call_lock(self.provider):
            response = self.provider.generate_chat(
                messages=normalized, tools=list(tools), tool_choice=tool_choice,
            )
            if not isinstance(response, Mapping) or set(response) - {"text", "tool_calls", "finish_reason", "usage"}:
                raise ValueError("provider returned an unsupported response envelope.")
            text = response.get("text", "")
            calls = response.get("tool_calls", {"tool_calls": []})
            if not isinstance(text, str) or not isinstance(calls, Mapping):
                raise ValueError("provider response fields are invalid.")
            normalized_response = dict(response)
            normalized_response["text"] = text
            normalized_response["tool_calls"] = dict(calls)
            # Capture mutable provider usage metadata before another request can
            # overwrite it on the shared adapter instance.
            self._record_usage(normalized, tools, normalized_response)
            return {"text": text, "tool_calls": dict(calls),
                    "finish_reason": response.get("finish_reason")}
