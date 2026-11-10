"""Composable ASGI runtime for authenticated Yuki conversations."""
from __future__ import annotations

from typing import Any, Callable

from api.conversations import YukiConversationAPI
from core.agent_execution_loop import AgentExecutionLoop
from core.auth import BuiltInAuthService
from core.conversation_service import ConversationService
from core.conversation_store_factory import create_conversation_store
from api.web import YukiWebApplication


class ConversationRuntime:
    """Own the API and its persistence resource for one application process."""

    def __init__(self, app: YukiConversationAPI, store: Any):
        self.app = app
        self.web = YukiWebApplication(app)
        self.store = store
        self._closed = False

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            await self.web(scope, receive, send)
            return
        if scope.get("type") != "lifespan":
            raise RuntimeError("ConversationRuntime supports HTTP and ASGI lifespan scopes.")
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                self.close()
                await send({"type": "lifespan.shutdown.complete"})
                return

    def close(self) -> None:
        """Close backend resources once during graceful application shutdown."""
        if self._closed:
            return
        close = getattr(self.store, "close", None)
        if callable(close):
            close()
        self._closed = True


def build_conversation_runtime(
    config: dict[str, Any],
    *,
    auth_service: BuiltInAuthService,
    agent_loop: AgentExecutionLoop,
    tools_for_user: Callable[[Any], list[dict[str, Any]]] | None = None,
    provider_catalog: Callable[[], list[dict[str, Any]]] | None = None,
    provider_selector: Callable[[str], dict[str, Any]] | None = None,
    device_agent: Any | None = None,
    reasoning_context_builder: Any | None = None,
    reasoning_agent_id: str = "yuki",
    memory_store: Any | None = None,
) -> ConversationRuntime:
    """Build an authenticated ASGI API with the configured persistence backend.

    The caller owns provider and auth construction. This function owns the
    conversation store and guarantees the selected backend is injected into the
    same service used by every API route.
    """
    if not isinstance(auth_service, BuiltInAuthService):
        raise TypeError("auth_service must be a BuiltInAuthService.")
    if not isinstance(agent_loop, AgentExecutionLoop):
        raise TypeError("agent_loop must be an AgentExecutionLoop.")
    store = create_conversation_store(config)
    try:
        service = ConversationService(
            store, agent_loop, reasoning_context_builder=reasoning_context_builder,
            reasoning_agent_id=reasoning_agent_id,
        )
        app = YukiConversationAPI(
            auth_service, service, tools_for_user=tools_for_user,
            provider_catalog=provider_catalog, provider_selector=provider_selector,
            device_agent=device_agent, memory_store=memory_store,
        )
        return ConversationRuntime(app, store)
    except Exception:
        close = getattr(store, "close", None)
        if callable(close):
            close()
        raise
