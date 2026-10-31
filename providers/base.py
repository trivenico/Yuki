"""Shared contracts and validation for Yuki model providers."""
from abc import ABC, abstractmethod


def validate_chat_messages(system: str, messages: list[dict], *, max_chars: int = 20_000) -> None:
    """Validate an explicitly separated system instruction and user/assistant history."""
    if not isinstance(system, str) or not system.strip():
        raise ValueError("System instructions must be non-empty text.")
    if len(system) > max_chars:
        raise ValueError("System instructions exceed the provider context limit.")
    if not isinstance(messages, list) or not messages or len(messages) > 50:
        raise ValueError("Chat messages must be a non-empty list of at most 50 items.")
    total = len(system)
    for item in messages:
        if not isinstance(item, dict) or set(item) != {"role", "content"}:
            raise ValueError("Each chat message must contain only role and content.")
        if item["role"] not in {"user", "assistant"}:
            raise ValueError("Only user and assistant history messages are accepted.")
        if not isinstance(item["content"], str) or not item["content"].strip():
            raise ValueError("Chat message content must be non-empty text.")
        total += len(item["content"])
    if messages[-1]["role"] != "user":
        raise ValueError("The final chat message must be from the user.")
    if total > max_chars:
        raise ValueError("Chat context exceeds the provider context limit.")


class BaseProvider(ABC):
    """Common interface for every AI provider Yuki supports."""

    @abstractmethod
    def generate(self, prompt: str) -> str:
        """Generate a response from a legacy single-prompt request."""
        raise NotImplementedError

    def generate_chat(self, system: str, messages: list[dict]) -> str:
        """Generate using distinct privileged system instructions and chat history.

        Providers that cannot preserve message roles must not emulate this by
        concatenating the system prompt into user-controlled text.
        """
        raise NotImplementedError("This provider does not support structured chat messages.")

    @abstractmethod
    def is_available(self) -> bool:
        """Report whether this provider is ready to use."""
        raise NotImplementedError
