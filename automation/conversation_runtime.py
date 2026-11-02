"""Provider-backed text conversation orchestration with optional streaming."""
from __future__ import annotations
from dataclasses import dataclass
from threading import Event
from typing import Any, Iterator
from automation.conversation_service import ConversationService

_MAX_HISTORY_EVENTS = 100
_MAX_PROMPT_CHARS = 18_000
_MAX_REPLY_CHARS = 32_000


class ConversationGenerationError(RuntimeError):
    """A model call failed or returned an invalid response."""


@dataclass(frozen=True)
class StreamEvent:
    type: str
    content: str = ""


class ConversationRuntime:
    def __init__(self, conversations: ConversationService):
        if not isinstance(conversations, ConversationService):
            raise TypeError("conversations must be a ConversationService")
        self.conversations = conversations

    @staticmethod
    def _provider(provider: Any) -> None:
        if not callable(getattr(provider, "generate", None)) or not callable(getattr(provider, "is_available", None)):
            raise TypeError("provider must implement generate(prompt) and is_available()")

    def build_prompt(self, *, actor_id: str, session_id: str) -> str:
        session = self.conversations.sessions.get(actor_id=actor_id, session_id=session_id)
        after_sequence = max(0, session.last_sequence - _MAX_HISTORY_EVENTS)
        events = self.conversations.history(actor_id=actor_id, session_id=session_id,
            after_sequence=after_sequence, limit=_MAX_HISTORY_EVENTS)
        messages = []
        for event in events:
            if event.kind not in {"message.user", "message.assistant", "message.system"}:
                continue
            content = event.payload.get("content")
            if isinstance(content, str) and content.strip():
                messages.append((event.kind.removeprefix("message."), content.strip()))
        if not messages:
            raise ValueError("conversation has no text messages")
        selected, remaining = [], _MAX_PROMPT_CHARS
        for role, content in reversed(messages):
            prefix = f"{role.upper()}: "
            available = remaining - len(prefix)
            if available <= 0:
                break
            if len(content) > available:
                if not selected:
                    content = content[-available:]
                else:
                    break
            selected.append(f"{prefix}{content}")
            remaining -= len(selected[-1]) + 2
            if remaining <= 0:
                break
        prompt = "\n\n".join(reversed(selected))
        if not prompt:
            raise ValueError("conversation prompt exceeds the configured size limit")
        return prompt

    def respond_with_manager(self, *, actor_id: str, session_id: str, idempotency_key: str,
        user_message: str, provider_manager: Any, preference=(), allow_cloud: bool = False) -> str:
        if not isinstance(allow_cloud, bool):
            raise TypeError("allow_cloud must be a boolean")
        self.conversations.sessions.get(actor_id=actor_id, session_id=session_id)
        if not isinstance(user_message, str) or not user_message.strip() or len(user_message) > 32_000:
            raise ValueError("user_message must be non-empty text within the message size limit")
        if not callable(getattr(provider_manager, "create_routed_adapter", None)):
            raise TypeError("provider_manager must implement create_routed_adapter()")
        provider = provider_manager.create_routed_adapter(required=("text",),
            preference=preference, allow_cloud=allow_cloud)
        return self.respond(actor_id=actor_id, session_id=session_id,
            idempotency_key=idempotency_key, user_message=user_message, provider=provider)

    def respond(self, *, actor_id: str, session_id: str, idempotency_key: str,
        user_message: str, provider: Any) -> str:
        self._provider(provider)
        if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 160:
            raise ValueError("idempotency_key must be non-empty text of at most 160 characters")
        key = idempotency_key.strip()
        self.conversations.add_message(actor_id=actor_id, session_id=session_id,
            idempotency_key=key, role="user", content=user_message)
        prompt = self.build_prompt(actor_id=actor_id, session_id=session_id)
        try:
            if provider.is_available() is not True:
                raise RuntimeError("selected provider is not available")
            reply = provider.generate(prompt)
            if not isinstance(reply, str) or not reply.strip() or len(reply) > _MAX_REPLY_CHARS:
                raise RuntimeError("provider returned an empty or oversized text response")
        except Exception as exc:
            self.conversations.sessions.append(actor_id=actor_id, session_id=session_id,
                idempotency_key=key + ":generation-failed", kind="generation.failed",
                payload={"error_type": type(exc).__name__})
            raise ConversationGenerationError("Selected model provider failed; no automatic fallback was attempted.") from exc
        self.conversations.add_message(actor_id=actor_id, session_id=session_id,
            idempotency_key=key + ":assistant", role="assistant", content=reply.strip())
        return reply.strip()

    def respond_stream(self, *, actor_id: str, session_id: str, idempotency_key: str,
        user_message: str, provider: Any, cancel_event: Event | None = None) -> Iterator[StreamEvent]:
        """Yield text deltas and persist a single assistant message only on clean completion."""
        self.conversations.sessions.get(actor_id=actor_id, session_id=session_id)
        if not isinstance(user_message, str) or not user_message.strip() or len(user_message) > 32_000:
            raise ValueError("user_message must be non-empty text within the message size limit")
        if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 160:
            raise ValueError("idempotency_key must be non-empty text of at most 160 characters")
        if not callable(getattr(provider, "stream", None)) or not callable(getattr(provider, "is_available", None)):
            raise TypeError("streaming provider must implement stream(prompt) and is_available()")
        key = idempotency_key.strip()
        self.conversations.add_message(actor_id=actor_id, session_id=session_id,
            idempotency_key=key, role="user", content=user_message)
        prompt = self.build_prompt(actor_id=actor_id, session_id=session_id)
        cancel = cancel_event if cancel_event is not None else Event()
        iterator, chunks, total, completed = None, [], 0, False
        try:
            if provider.is_available() is not True:
                raise RuntimeError("selected provider is not available")
            iterator = iter(provider.stream(prompt))
            for chunk in iterator:
                if cancel.is_set():
                    break
                if not isinstance(chunk, str):
                    raise RuntimeError("provider stream yielded a non-text chunk")
                if not chunk:
                    continue
                total += len(chunk)
                if total > _MAX_REPLY_CHARS:
                    raise RuntimeError("provider stream exceeded the response size limit")
                chunks.append(chunk)
                yield StreamEvent("delta", chunk)
            if cancel.is_set():
                self.conversations.sessions.append(actor_id=actor_id, session_id=session_id,
                    idempotency_key=key + ":generation-interrupted", kind="generation.interrupted",
                    payload={"partial_characters": total})
                yield StreamEvent("interrupted")
                return
            reply = "".join(chunks).strip()
            if not reply:
                raise RuntimeError("provider stream returned no text")
            self.conversations.add_message(actor_id=actor_id, session_id=session_id,
                idempotency_key=key + ":assistant", role="assistant", content=reply)
            completed = True
            yield StreamEvent("completed", reply)
        except GeneratorExit:
            raise
        except Exception as exc:
            self.conversations.sessions.append(actor_id=actor_id, session_id=session_id,
                idempotency_key=key + ":generation-failed", kind="generation.failed",
                payload={"error_type": type(exc).__name__, "partial_characters": total})
            raise ConversationGenerationError("Streaming provider failed; partial output was not persisted.") from exc
        finally:
            if iterator is not None:
                close = getattr(iterator, "close", None)
                if callable(close):
                    close()
