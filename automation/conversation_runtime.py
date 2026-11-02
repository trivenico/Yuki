"""Provider-backed text conversation orchestration.

Provider selection and authorization remain the caller's responsibility. This
runtime deliberately has no tool execution, streaming, cancellation or approval
side effects; it only generates and persists text responses.
"""
from __future__ import annotations

from typing import Any

from automation.conversation_service import ConversationService

_MAX_HISTORY_EVENTS = 100
_MAX_PROMPT_CHARS = 100_000
_MAX_REPLY_CHARS = 32_000


class ConversationGenerationError(RuntimeError):
    """A model call failed or returned an invalid response."""


class ConversationRuntime:
    def __init__(self, conversations: ConversationService):
        if not isinstance(conversations, ConversationService):
            raise TypeError("conversations must be a ConversationService")
        self.conversations = conversations

    @staticmethod
    def _provider(provider: Any) -> None:
        if not callable(getattr(provider, "generate", None)):
            raise TypeError("provider must implement generate(prompt)")
        if not callable(getattr(provider, "is_available", None)):
            raise TypeError("provider must implement is_available()")

    def build_prompt(self, *, actor_id: str, session_id: str) -> str:
        events = self.conversations.history(
            actor_id=actor_id, session_id=session_id, limit=_MAX_HISTORY_EVENTS
        )
        lines = []
        for event in events:
            if event.kind not in {"message.user", "message.assistant", "message.system"}:
                continue
            content = event.payload.get("content")
            if not isinstance(content, str):
                continue
            role = event.kind.removeprefix("message.")
            lines.append(f"{role.upper()}: {content}")
        prompt = "\n\n".join(lines)
        if not prompt:
            raise ValueError("conversation has no text messages")
        if len(prompt) > _MAX_PROMPT_CHARS:
            raise ValueError("conversation prompt exceeds the configured size limit")
        return prompt

    def respond(
        self, *, actor_id: str, session_id: str, idempotency_key: str,
        user_message: str, provider: Any,
    ) -> str:
        """Persist user input, generate one text reply, then persist the reply.

        The caller must supply a provider already selected under its model and
        cloud-consent policy. A failed request is recorded for diagnosis and is
        not silently retried or routed to another provider.
        """
        self._provider(provider)
        if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 160:
            raise ValueError("idempotency_key must be non-empty text of at most 160 characters")
        user_key = idempotency_key.strip()
        self.conversations.add_message(
            actor_id=actor_id, session_id=session_id,
            idempotency_key=user_key, role="user", content=user_message,
        )
        prompt = self.build_prompt(actor_id=actor_id, session_id=session_id)
        try:
            if provider.is_available() is not True:
                raise RuntimeError("selected provider is not available")
            reply = provider.generate(prompt)
            if not isinstance(reply, str) or not reply.strip() or len(reply) > _MAX_REPLY_CHARS:
                raise RuntimeError("provider returned an empty or oversized text response")
        except Exception as exc:
            # This is an audit event only; it is never interpreted as a retry command.
            self.conversations.sessions.append(
                actor_id=actor_id, session_id=session_id,
                idempotency_key=user_key + ":generation-failed",
                kind="generation.failed",
                payload={"error_type": type(exc).__name__},
            )
            raise ConversationGenerationError(
                "Selected model provider failed; no automatic fallback was attempted."
            ) from exc
        self.conversations.add_message(
            actor_id=actor_id, session_id=session_id,
            idempotency_key=user_key + ":assistant",
            role="assistant", content=reply.strip(),
        )
        return reply.strip()
