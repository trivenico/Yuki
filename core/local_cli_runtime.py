"""Local CLI composition for durable, owner-scoped assistant conversations.

The CLI deliberately starts with an empty guarded tool registry. Persistent chat
uses the shared conversation application runtime used by the API, but does not
grant the terminal an implicit ability to execute tools.
"""
from __future__ import annotations

from collections.abc import Mapping
from threading import RLock
from typing import Any, Callable

from core.agent_execution_loop import AgentExecutionLoop
from core.core_component_runtime import build_local_core_component_registry
from core.configuration_component_runtime import build_configuration_component_runtime
from core.conversation_application_runtime import (
    ConversationApplicationRuntime,
    build_conversation_application_runtime,
)
from core.guarded_tool_runtime import GuardedToolRuntime
from core.local_owner_identity import load_or_create_local_owner_id
from core.readiness import ReadinessReport, build_readiness_report
from core.structured_tool_protocol import StructuredToolProtocol
from providers.agent_adapter import ProviderAgentAdapter
from providers.factory import create_provider


def _materialize_snapshot_value(value: Any) -> Any:
    """Create an isolated mutable adapter value from immutable snapshot data."""
    if isinstance(value, Mapping):
        return {key: _materialize_snapshot_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_materialize_snapshot_value(item) for item in value]
    if isinstance(value, frozenset):
        return {_materialize_snapshot_value(item) for item in value}
    return value


class _DenyAllLocalTools:
    """No CLI tool can run until an explicit, separately reviewed registry exists."""

    def authorize_tool(self, name: str) -> bool:
        return False


class _OwnedProvider:
    """Idempotent ownership fence shared by CLI and component cleanup paths."""
    def __init__(self, provider: Any):
        self._provider = provider
        self._closed = False

    def generate(self, *args, **kwargs):
        return self._provider.generate(*args, **kwargs)

    def is_available(self):
        return self._provider.is_available()

    def close(self):
        if self._closed:
            return
        self._closed = True
        close = getattr(self._provider, "close", None)
        if callable(close):
            close()


class LocalConversationRuntime:
    """CLI-facing handle whose component registry owns runtime shutdown."""
    def __init__(self, application: ConversationApplicationRuntime, component_registry):
        self.application = application
        self.component_registry = component_registry
        self._closed = False
        self._close_lock = RLock()

    def __getattr__(self, name):
        return getattr(self.application, name)

    def readiness_report(self) -> ReadinessReport:
        """Return local capability diagnostics including live component health."""
        return build_readiness_report(component_registry=self.component_registry)

    def close(self):
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
            failures = self.component_registry.close()
            if failures:
                raise RuntimeError("local CLI component shutdown failed: " + ", ".join(failures))


def build_local_conversation_runtime(
    config: dict[str, Any],
    *,
    provider_factory: Callable[..., Any] = create_provider,
    store_factory: Callable[[dict[str, Any]], Any] | None = None,
    owner_id_factory: Callable[[], str] | None = None,
) -> LocalConversationRuntime:
    """Build the installed CLI's persistent chat service with fail-closed tools."""
    if not isinstance(config, dict):
        raise TypeError("config must be a dictionary.")
    # Freeze complete configuration before constructing dependent runtimes.
    configuration_runtime = None
    if (
        isinstance(config.get("workspace"), str)
        and config.get("workspace", "").strip()
        and isinstance(config.get("provider"), str)
        and config.get("provider", "").strip()
    ):
        configuration_runtime = build_configuration_component_runtime(config)
        snapshot_values = configuration_runtime.instance.values
        provider_config = _materialize_snapshot_value(snapshot_values)
        runtime_config = _materialize_snapshot_value(snapshot_values)
    else:
        provider_config = dict(config)
        runtime_config = dict(config)

    provider = provider_factory(provider_config)
    owned_provider = _OwnedProvider(provider)
    application = None
    registry = None
    try:
        model = ProviderAgentAdapter(provider)
        tool_runtime = GuardedToolRuntime({}, _DenyAllLocalTools())
        protocol = StructuredToolProtocol(tool_runtime)
        max_turns = runtime_config.get("max_agent_steps", 8)
        if type(max_turns) is not int or not 1 <= max_turns <= 100:
            raise ValueError("max_agent_steps must be an integer from 1 to 100.")
        agent_loop = AgentExecutionLoop(
            model,
            protocol,
            stream_model=(
                model.stream_model
                if callable(getattr(provider, "generate_chat_stream", None)) else None
            ),
            max_model_turns=max_turns,
        )
        owner_id = (
            owner_id_factory() if owner_id_factory is not None
            else load_or_create_local_owner_id(runtime_config.get("workspace"))
        )
        if not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 128:
            raise ValueError("local owner identity is invalid.")
        application = build_conversation_application_runtime(
            runtime_config, agent_loop=agent_loop, owner_id=owner_id,
            **({"store_factory": store_factory} if store_factory is not None else {}),
        )
        registry = build_local_core_component_registry(
            owner_id=owner_id, provider=owned_provider,
            agent_loop=agent_loop, application_runtime=application,
            config=runtime_config if configuration_runtime is not None else None,
        )
        registry.start_all()
        return LocalConversationRuntime(application, registry)
    except Exception:
        if registry is not None:
            registry.close()
        if application is not None:
            try:
                application.close()
            except Exception:
                pass
        try:
            owned_provider.close()
        except Exception:
            pass
        raise
