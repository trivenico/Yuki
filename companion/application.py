"""Shared application service for Yuki's companion clients.

CLI and desktop frontends should call this service rather than implement their own
conversation/memory policy. This layer owns the conversation session and the
session-scoped consent facade; it does not execute tools or dispatch tasks.
"""
from __future__ import annotations

from typing import Any

from companion.conversation import ConversationError, ConversationReply, ConversationSession
from companion.memory_consent import CompanionMemory


class CompanionApplicationService:
    """One companion session API shared by terminal and graphical clients."""

    def __init__(
        self, provider: Any, *, memory: CompanionMemory | None = None,
        session: ConversationSession | None = None,
    ) -> None:
        if memory is not None and not isinstance(memory, CompanionMemory):
            raise TypeError("memory must be a CompanionMemory instance.")
        if session is not None and not isinstance(session, ConversationSession):
            raise TypeError("session must be a ConversationSession instance.")
        if session is not None and session.provider is not provider:
            raise ValueError("session and application service must use the same provider.")
        self.memory = memory or CompanionMemory()
        self.session = session or ConversationSession(provider)

    def respond(self, message: str) -> ConversationReply:
        """Respond using only explicitly enabled, relevant saved-memory context."""
        try:
            context = self.memory.context_entries(query=message)
        except Exception as exc:
            raise ConversationError("Yuki could not safely read saved-memory context; no request was sent.") from exc
        return self.session.respond(message, memory_context=context)

    def set_role(self, name: str) -> dict:
        return self.session.set_role(name)

    def current_role(self) -> dict:
        return self.session.current_role()

    def reset_conversation(self) -> None:
        self.session.reset()

    def memory_status(self) -> dict[str, bool]:
        return {
            "saving_enabled": self.memory.enabled,
            "context_enabled": self.memory.context_enabled,
        }
