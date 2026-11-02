"""Adapt Yuki provider APIs to the message-based bounded agent loop."""
from __future__ import annotations

from typing import Any, Mapping, Sequence


class ProviderAgentAdapter:
    """Present a provider's chat/tool-call API as an AgentExecutionLoop model callable.

    Providers must expose generate_chat(messages, tools, tool_choice). This
    explicit contract avoids lossy transcript flattening and keeps tool
    execution exclusively in StructuredToolProtocol.
    """

    def __init__(self, provider: Any):
        if provider is None or not callable(getattr(provider, "generate_chat", None)):
            raise TypeError("provider must implement generate_chat(messages, tools, tool_choice).")
        self.provider = provider

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
            if role not in {"system", "user", "assistant"} or not isinstance(content, str):
                raise ValueError("message role or content is invalid.")
            total_chars += len(content)
            if total_chars > 100_000:
                raise ValueError("message transcript exceeds the character limit.")
            normalized.append({"role": role, "content": content})
        if not isinstance(tools, (list, tuple)) or len(tools) > 32:
            raise ValueError("tools must contain at most 32 definitions.")
        if tool_choice not in {"auto", "none", "required"}:
            raise ValueError("tool_choice is invalid.")
        response = self.provider.generate_chat(
            messages=normalized, tools=list(tools), tool_choice=tool_choice,
        )
        if not isinstance(response, Mapping) or set(response) - {"text", "tool_calls", "finish_reason"}:
            raise ValueError("provider returned an unsupported response envelope.")
        text = response.get("text", "")
        calls = response.get("tool_calls", {"tool_calls": []})
        if not isinstance(text, str) or not isinstance(calls, Mapping):
            raise ValueError("provider response fields are invalid.")
        return {"text": text, "tool_calls": dict(calls),
                "finish_reason": response.get("finish_reason")}
