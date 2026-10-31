"""Shared application composition for Yuki's CLI and future desktop interfaces.

Interfaces should depend on this lifecycle boundary rather than constructing
provider, agent, and conversation services independently.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from core.conversation_store import ConversationStore
from yuki_agent import YukiAgent
from yuki_providers import ProviderManager


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class YukiApplicationRuntime:
    """Own the shared AI and conversation services for one Yuki application."""

    def __init__(self, providers, agent, conversations, active_conversation_id):
        if providers is None or agent is None or conversations is None:
            raise ValueError("Provider, agent and conversation services are required.")
        self.providers = providers
        self.agent = agent
        self.conversations = conversations
        self.active_conversation_id = active_conversation_id
        self._closed = False

    @classmethod
    def create(
        cls,
        *,
        providers_factory: Callable = ProviderManager,
        agent_factory: Callable = YukiAgent,
        conversation_factory: Callable = ConversationStore,
        session_title: str = "Yuki session",
    ) -> "YukiApplicationRuntime":
        providers = providers_factory()
        adapter = providers.create_selected_adapter()
        agent = agent_factory(provider=adapter)
        conversations = conversation_factory()
        try:
            active_id = conversations.create(session_title, utc_now())
        except Exception:
            conversations.close()
            raise
        return cls(providers, agent, conversations, active_id)

    def begin_turn(self, content: str) -> str:
        self._ensure_open()
        if not isinstance(content, str) or not content.strip():
            raise ValueError("A non-empty user message is required.")
        return self.conversations.append(
            self.active_conversation_id, "user", content, utc_now()
        )

    def finish_turn(self, content: str) -> str:
        self._ensure_open()
        if not isinstance(content, str) or not content.strip():
            raise ValueError("A non-empty assistant response is required.")
        return self.conversations.append(
            self.active_conversation_id, "assistant", content, utc_now()
        )

    def switch_conversation(self, conversation_id: str) -> None:
        self._ensure_open()
        known = {item["conversation_id"] for item in self.conversations.list_conversations()}
        if conversation_id not in known:
            raise ValueError("Conversation does not exist on this device.")
        self.active_conversation_id = conversation_id

    def stream_message(self, content: str):
        """Stream a turn while persisting the final assembled assistant response."""
        self.begin_turn(content)
        chunks = []
        try:
            for chunk in self.agent.stream(content):
                if not isinstance(chunk, str):
                    raise RuntimeError("Provider emitted a non-text stream chunk.")
                if chunk:
                    chunks.append(chunk)
                    yield chunk
        except Exception:
            # Keep the user's message, but never persist a partial assistant turn
            # as if it were a completed answer.
            raise
        response = "".join(chunks).strip()
        if response:
            self.finish_turn(response)

    def send_message(self, content: str) -> str:
        """Run a normal chat turn through the shared provider and persist both sides."""
        return "".join(self.stream_message(content))

    def new_conversation(self, title: str = "Yuki session") -> str:
        self._ensure_open()
        conversation_id = self.conversations.create(title, utc_now())
        self.active_conversation_id = conversation_id
        return conversation_id

    def history(self):
        self._ensure_open()
        return self.conversations.messages(self.active_conversation_id)

    def _ensure_open(self):
        if self._closed:
            raise RuntimeError("Yuki application runtime is closed.")

    def close(self):
        if not self._closed:
            self.conversations.close()
            self._closed = True
