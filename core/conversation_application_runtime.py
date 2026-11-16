"""Shared composition and lifecycle for Yuki conversation surfaces.

The API and installed CLI use this runtime so persistence, conversation-service
construction, owner scoping and shutdown semantics do not diverge.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable
from threading import RLock

from core.agent_execution_loop import AgentExecutionLoop
from core.conversation_service import ConversationService
from core.conversation_store_factory import create_conversation_store
from core.conversation_memory_store import ConversationMemoryStore
from core.guarded_tool_runtime import GuardedToolRuntime
from core.structured_tool_protocol import StructuredToolProtocol
from providers.agent_adapter import ProviderAgentAdapter


@dataclass
class ConversationApplicationRuntime:
    """Own one conversation service, its store, and its optional local owner scope."""

    conversations: ConversationService
    store: Any
    owner_id: str | None = None
    provider_adapter: ProviderAgentAdapter | None = None
    close_callbacks: tuple[Callable[[], None], ...] = ()
    _closed: bool = False
    _lock: RLock = field(default_factory=RLock, repr=False, compare=False)

    @property
    def closed(self) -> bool:
        """Whether application shutdown has begun, including failed cleanup."""
        with self._lock:
            return self._closed

    def close(self) -> None:
        """Close persistence exactly once; concurrent callers share one cleanup."""
        with self._lock:
            if self._closed:
                return
            # Mark shutdown before invoking user/provider-owned cleanup so a
            # failing close hook is never called twice by nested shutdown paths.
            self._closed = True
            errors: list[Exception] = []
            try:
                self.conversations.close()
            except Exception as exc:
                errors.append(exc)
            close = getattr(self.store, "close", None)
            hooks = ((close,) if callable(close) else ()) + self.close_callbacks
            # The store is created after externally supplied resources, so close it
            # first, then release those resources even if one cleanup hook fails.
            for hook in hooks:
                try:
                    hook()
                except Exception as exc:
                    errors.append(exc)
            if errors:
                raise errors[0]


def build_conversation_application_runtime(
    config: dict[str, Any],
    *,
    agent_loop: AgentExecutionLoop,
    owner_id: str | None = None,
    store_factory: Callable[[dict[str, Any]], Any] = create_conversation_store,
    agent_loop_for_owner: Callable[[str], AgentExecutionLoop] | None = None,
    close_callbacks: tuple[Callable[[], None], ...] = (),
    action_context_factory: Callable | None = None,
    confirmation_check: Callable | None = None,
    approval_id_factory: Callable | None = None,
    pending_proposal_factory: Callable | None = None,
    memory_store: ConversationMemoryStore | None = None,
) -> ConversationApplicationRuntime:
    """Compose the common durable conversation core for API, CLI and companion.

    Authentication remains a surface responsibility: API owners are resolved
    from authenticated sessions, while a local CLI may supply its local owner
    scope. This function never authenticates a network caller or grants tools.
    """
    if not isinstance(config, dict):
        raise TypeError("config must be a dictionary.")
    if not isinstance(agent_loop, AgentExecutionLoop):
        raise TypeError("agent_loop must be an AgentExecutionLoop.")
    if agent_loop_for_owner is not None and not callable(agent_loop_for_owner):
        raise TypeError("agent_loop_for_owner must be callable.")
    if not callable(store_factory):
        raise TypeError("store_factory must be callable.")
    if memory_store is not None and not isinstance(memory_store, ConversationMemoryStore):
        raise TypeError("memory_store must be a ConversationMemoryStore or None.")
    for callback, name in (
        (action_context_factory, "action_context_factory"),
        (confirmation_check, "confirmation_check"),
        (approval_id_factory, "approval_id_factory"),
        (pending_proposal_factory, "pending_proposal_factory"),
    ):
        if callback is not None and not callable(callback):
            raise TypeError(f"{name} must be callable.")
    if not isinstance(close_callbacks, tuple) or any(
        not callable(callback) for callback in close_callbacks
    ):
        raise TypeError("close_callbacks must be a tuple of callable cleanup hooks.")
    if owner_id is not None and (
        not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 128
    ):
        raise ValueError("owner_id must be non-empty text of at most 128 characters.")
    try:
        store = store_factory(config)
    except Exception:
        for callback in close_callbacks:
            try:
                callback()
            except Exception:
                pass
        raise
    try:
        conversations = ConversationService(
            store, agent_loop, agent_loop_for_owner=agent_loop_for_owner,
            action_context_factory=action_context_factory,
            confirmation_check=confirmation_check,
            approval_id_factory=approval_id_factory,
            pending_proposal_factory=pending_proposal_factory,
            memory_store=memory_store,
        )
        return ConversationApplicationRuntime(
            conversations=conversations, store=store, owner_id=owner_id,
            close_callbacks=close_callbacks,
        )
    except Exception:
        close = getattr(store, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
        for callback in close_callbacks:
            try:
                callback()
            except Exception:
                pass
        raise



def build_provider_backed_conversation_application_runtime(
    config: dict[str, Any],
    *,
    provider: Any,
    guarded_runtime: GuardedToolRuntime,
    owner_id: str | None = None,
    store_factory: Callable[[dict[str, Any]], Any] = create_conversation_store,
    agent_loop_for_owner: Callable[[str], AgentExecutionLoop] | None = None,
    close_callbacks: tuple[Callable[[], None], ...] = (),
    memory_store: ConversationMemoryStore | None = None,
    max_model_turns: int = 8,
    max_tool_calls: int = 20,
    max_duration_seconds: float = 120,
    max_transcript_chars: int = 80_000,
) -> ConversationApplicationRuntime:
    """Build the canonical provider -> bounded agent -> guarded tools -> durable chat stack.

    The provider is owned by this runtime and is closed after conversations are
    fenced and persistence is closed. Tool authorization remains the supplied
    GuardedToolRuntime's responsibility; this factory never grants capabilities.
    """
    if not isinstance(guarded_runtime, GuardedToolRuntime):
        raise TypeError("guarded_runtime must be a GuardedToolRuntime.")
    adapter = ProviderAgentAdapter(provider)
    loop = AgentExecutionLoop(
        adapter, StructuredToolProtocol(guarded_runtime),
        stream_model=adapter.stream_model,
        max_model_turns=max_model_turns,
        max_tool_calls=max_tool_calls,
        max_duration_seconds=max_duration_seconds,
        max_transcript_chars=max_transcript_chars,
    )
    provider_close = getattr(provider, "close", None)
    if provider_close is not None and not callable(provider_close):
        raise TypeError("provider close attribute must be callable when present.")
    provider_close_lock = RLock()
    provider_closed = False

    def close_provider_once() -> None:
        nonlocal provider_closed
        with provider_close_lock:
            if provider_closed:
                return
            provider_closed = True
        if callable(provider_close):
            provider_close()

    callbacks = ((close_provider_once,) if callable(provider_close) else ()) + close_callbacks
    try:
        runtime = build_conversation_application_runtime(
            config, agent_loop=loop, owner_id=owner_id, store_factory=store_factory,
            agent_loop_for_owner=agent_loop_for_owner, close_callbacks=callbacks,
            memory_store=memory_store,
        )
    except Exception:
        # The lower-level builder invokes cleanup callbacks after resource
        # construction failures; this idempotent guard also covers validation
        # failures that happen before it takes ownership.
        try:
            close_provider_once()
        except Exception:
            pass
        raise
    runtime.provider_adapter = adapter
    return runtime
