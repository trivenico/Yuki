"""Composable ASGI runtime for authenticated Yuki conversations."""
from __future__ import annotations

from typing import Any, Callable

from api.conversations import YukiConversationAPI
from api.income import AuthenticatedIncomeOpportunityAPI
from api.inference import AuthenticatedInferenceAPI
from api.oauth_connections import AuthenticatedOAuthConnectionAPI
from api.oauth_authorization import AuthenticatedOAuthAuthorizationAPI
from api.web import YukiWebApplication
from core.agent_execution_loop import AgentExecutionLoop
from core.auth import BuiltInAuthService
from core.conversation_service import ConversationService
from core.conversation_store_factory import create_conversation_store
from core.device_pairing import DevicePairingService
from core.income_opportunities import SQLiteIncomeOpportunityStore
from core.inference_jobs import InferenceJobService
from core.oauth_connection_registry import SQLiteOAuthConnectionRegistry


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
    device_pairing: DevicePairingService | None = None,
    inference_jobs: InferenceJobService | None = None,
    income_opportunities: SQLiteIncomeOpportunityStore | None = None,
    oauth_connections: SQLiteOAuthConnectionRegistry | None = None,
    oauth_disconnect_service_for_provider: Callable[[str], Any] | None = None,
    oauth_authorization_service_for_provider: Callable[[str], Any] | None = None,
) -> ConversationRuntime:
    """Build authenticated APIs around the configured conversation runtime.

    Provider/auth construction belongs to the caller. The conversation store
    is owned here; optional workflow stores use per-operation SQLite connections.
    """
    if not isinstance(auth_service, BuiltInAuthService):
        raise TypeError("auth_service must be a BuiltInAuthService.")
    if not isinstance(agent_loop, AgentExecutionLoop):
        raise TypeError("agent_loop must be an AgentExecutionLoop.")
    if (device_pairing is None) != (inference_jobs is None):
        raise ValueError("device_pairing and inference_jobs must be configured together")
    if device_pairing is not None and inference_jobs.pairing is not device_pairing:
        raise ValueError("inference_jobs must use the configured device_pairing service")
    if income_opportunities is not None and not isinstance(
        income_opportunities, SQLiteIncomeOpportunityStore
    ):
        raise TypeError("income_opportunities must be a SQLiteIncomeOpportunityStore")
    if oauth_connections is not None and not isinstance(
        oauth_connections, SQLiteOAuthConnectionRegistry
    ):
        raise TypeError("oauth_connections must be a SQLiteOAuthConnectionRegistry")
    if (oauth_connections is None) != (oauth_disconnect_service_for_provider is None):
        raise ValueError("oauth_connections and disconnect service resolver must be configured together")
    if oauth_authorization_service_for_provider is not None and oauth_connections is None:
        raise ValueError("OAuth authorization requires the OAuth connection registry")
    if oauth_authorization_service_for_provider is not None and not callable(oauth_authorization_service_for_provider):
        raise TypeError("oauth_authorization_service_for_provider must be callable")
    store = create_conversation_store(config)
    try:
        service = ConversationService(store, agent_loop)
        app = YukiConversationAPI(
            auth_service, service, tools_for_user=tools_for_user,
            provider_catalog=provider_catalog, provider_selector=provider_selector,
        )
        if device_pairing is not None:
            app = AuthenticatedInferenceAPI(app, auth_service, device_pairing, inference_jobs)
        if income_opportunities is not None:
            app = AuthenticatedIncomeOpportunityAPI(app, auth_service, income_opportunities)
        if oauth_connections is not None:
            app = AuthenticatedOAuthConnectionAPI(
                app, auth_service, oauth_connections,
                oauth_disconnect_service_for_provider,
            )
            if oauth_authorization_service_for_provider is not None:
                app = AuthenticatedOAuthAuthorizationAPI(
                    app, auth_service, oauth_connections,
                    oauth_authorization_service_for_provider,
                )
        return ConversationRuntime(app, store)
    except Exception:
        close = getattr(store, "close", None)
        if callable(close):
            close()
        raise
