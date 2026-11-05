"""Construct an owner-isolated reasoning execution loop per authenticated request.

Provider selection and tool authorization remain application-owned callbacks.
This module never accepts an actor identity from an HTTP payload.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from core.agent_execution_loop import AgentExecutionLoop
from core.owner_identity import memory_actor_id_for_owner
from core.reasoning.context_builder import ReasoningContextBuilder
from core.structured_tool_protocol import StructuredToolProtocol
from providers.agent_adapter import ProviderAgentAdapter
from providers.reasoning_agent_adapter import ReasoningAgentAdapter


def create_owner_scoped_agent_loop_factory(
    *,
    provider_adapter_for_owner: Callable[[str], ProviderAgentAdapter],
    context_builder: ReasoningContextBuilder,
    tool_protocol_for_owner: Callable[[str], StructuredToolProtocol],
    agent_id: str,
    system_policy: str,
    owner_preferences_for_owner: Callable[[str], Sequence[str]] | None = None,
    include_shared: bool = False,
    include_unverified: bool = False,
    evidence_limit: int = 8,
    max_context_chars: int = 12_000,
    execution_options: dict[str, Any] | None = None,
    provider_adapter_decorator_for_owner: Callable[[str, ProviderAgentAdapter], ProviderAgentAdapter] | None = None,
) -> Callable[[str], AgentExecutionLoop]:
    """Return a factory suitable for ConversationService(agent_loop_factory=...).

    The authenticated owner ID is supplied by ConversationService after its
    ownership/idempotency checks. Each invocation creates a fresh reasoning
    adapter and execution loop, preventing actor identity from being stored in
    a process-wide singleton. Shared memory is opt-in and still requires the
    memory store's independent agent_memory:share authorization.
    """
    if not callable(provider_adapter_for_owner):
        raise TypeError("provider_adapter_for_owner_must_be_callable")
    if not isinstance(context_builder, ReasoningContextBuilder):
        raise TypeError("context_builder_must_be_ReasoningContextBuilder")
    if not callable(tool_protocol_for_owner):
        raise TypeError("tool_protocol_for_owner_must_be_callable")
    if not isinstance(agent_id, str) or not agent_id.strip() or len(agent_id) > 200:
        raise ValueError("invalid_agent_id")
    if not isinstance(system_policy, str) or not system_policy.strip():
        raise ValueError("system_policy_required")
    if owner_preferences_for_owner is not None and not callable(owner_preferences_for_owner):
        raise TypeError("owner_preferences_for_owner_must_be_callable")
    if provider_adapter_decorator_for_owner is not None and not callable(provider_adapter_decorator_for_owner):
        raise TypeError("provider_adapter_decorator_for_owner_must_be_callable")
    if type(include_shared) is not bool or type(include_unverified) is not bool:
        raise ValueError("retrieval_flags_must_be_boolean")
    if type(evidence_limit) is not int or not 1 <= evidence_limit <= 20:
        raise ValueError("evidence_limit_must_be_1_to_20")
    if type(max_context_chars) is not int or not 512 <= max_context_chars <= 32_000:
        raise ValueError("max_context_chars_out_of_range")
    options = {} if execution_options is None else dict(execution_options)
    allowed_options = {
        "max_model_turns", "max_tool_calls", "max_duration_seconds",
        "max_transcript_chars",
    }
    if set(options) - allowed_options:
        raise ValueError("unsupported_execution_options")

    def build(owner_id: str) -> AgentExecutionLoop:
        if not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 200:
            raise ValueError("invalid_authenticated_owner_id")
        owner = owner_id.strip()
        provider_adapter = provider_adapter_for_owner(owner)
        if not isinstance(provider_adapter, ProviderAgentAdapter):
            raise TypeError("provider_factory_must_return_ProviderAgentAdapter")
        if provider_adapter_decorator_for_owner is not None:
            provider_adapter = provider_adapter_decorator_for_owner(owner, provider_adapter)
            if not isinstance(provider_adapter, ProviderAgentAdapter):
                raise TypeError("provider_adapter_decorator_must_return_ProviderAgentAdapter")
        tool_protocol = tool_protocol_for_owner(owner)
        if not isinstance(tool_protocol, StructuredToolProtocol):
            raise TypeError("tool_protocol_factory_must_return_StructuredToolProtocol")
        preferences = (
            owner_preferences_for_owner(owner)
            if owner_preferences_for_owner is not None else ()
        )
        if not isinstance(preferences, (list, tuple)):
            raise TypeError("owner_preferences_factory_must_return_sequence")
        # Authentication IDs may be URL-safe tokens beginning with '_' or '-'.
        # Map them to a stable, opaque, store-safe owner key before memory access.
        memory_actor_id = memory_actor_id_for_owner(owner)
        reasoning_adapter = ReasoningAgentAdapter(
            provider_adapter,
            context_builder,
            actor_id=memory_actor_id,
            agent_id=agent_id.strip(),
            system_policy=system_policy,
            owner_preferences=preferences,
            include_shared=include_shared,
            include_unverified=include_unverified,
            evidence_limit=evidence_limit,
            max_context_chars=max_context_chars,
        )
        stream_model = (
            reasoning_adapter.stream_model
            if callable(getattr(provider_adapter.provider, "generate_chat_stream", None))
            else None
        )
        return AgentExecutionLoop(
            reasoning_adapter, tool_protocol,
            stream_model=stream_model, **options,
        )

    return build
