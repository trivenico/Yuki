"""Composable ASGI runtime for authenticated Yuki conversations."""
from __future__ import annotations

from threading import RLock
from typing import Any, Callable

from api.conversations import YukiConversationAPI
from api.income import AuthenticatedIncomeOpportunityAPI
from api.inference import AuthenticatedInferenceAPI
from api.evaluations import AuthenticatedEvaluationAPI
from api.mcp_management import AuthenticatedMCPManagementAPI
from api.oauth_connections import AuthenticatedOAuthConnectionAPI
from api.oauth_authorization import AuthenticatedOAuthAuthorizationAPI
from api.web import YukiWebApplication
from core.agent_execution_loop import AgentExecutionLoop
from core.auth import BuiltInAuthService
from core.component_registry import ComponentRegistry
from core.configuration_component_runtime import build_configuration_component_runtime
from core.conversation_application_runtime import (
    ConversationApplicationRuntime,
    build_conversation_application_runtime,
)
from core.device_pairing import DevicePairingService
from core.readiness import ReadinessReport, build_readiness_report
from core.income_opportunities import SQLiteIncomeOpportunityStore
from core.inference_jobs import InferenceJobService
from core.oauth_connection_registry import SQLiteOAuthConnectionRegistry
from core.oauth_provider_registry import PreparedOAuthProviderRegistry
from core.mcp_runtime import MCPRuntimeService
from evaluation.service import EvaluationService


class ConversationRuntime:
    """Own the authenticated API and shared conversation application lifecycle."""

    def __init__(self, app: Any, application: ConversationApplicationRuntime, component_registry: ComponentRegistry | None = None):
        self.app = app
        self.web = YukiWebApplication(app)
        self.application = application
        self.component_registry = component_registry
        self.store = application.store
        self._closed = False
        self._close_lock = RLock()

    async def __call__(self, scope, receive, send):
        if self._closed:
            raise RuntimeError("conversation runtime is closed")
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

    def readiness_report(self) -> ReadinessReport:
        """Return capability diagnostics with this runtime component health."""
        return build_readiness_report(component_registry=self.component_registry)

    def close(self) -> None:
        """Close backend resources once during graceful application shutdown."""
        with self._close_lock:
            if self._closed:
                return
            # Serialize concurrent callers until resource teardown has finished.
            # Keep the closed fence set even if a cleanup hook raises: cleanup
            # is not retried implicitly against potentially partially closed resources.
            self._closed = True
            if self.component_registry is None:
                self.application.close()
                return
            failures = self.component_registry.close()
            if failures:
                raise RuntimeError("component shutdown failed: " + ", ".join(failures))


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
    oauth_provider_catalog: Callable[[], list[dict[str, Any]]] | None = None,
    prepared_oauth_provider_registry: PreparedOAuthProviderRegistry | None = None,
    mcp_runtime: MCPRuntimeService | None = None,
    evaluation_service: EvaluationService | None = None,
    agent_loop_for_owner: Callable[[str], AgentExecutionLoop] | None = None,
    component_registry_factory: Callable[[BuiltInAuthService, AgentExecutionLoop, ConversationApplicationRuntime], ComponentRegistry] | None = None,
) -> ConversationRuntime:
    """Build authenticated APIs around the shared conversation application core.

    Provider/auth construction belongs to the caller. API owner identity is
    always derived by the authenticated API from its session, never from config.
    """
    if not isinstance(auth_service, BuiltInAuthService):
        raise TypeError("auth_service must be a BuiltInAuthService.")
    if not isinstance(agent_loop, AgentExecutionLoop):
        raise TypeError("agent_loop must be an AgentExecutionLoop.")
    if agent_loop_for_owner is not None and not callable(agent_loop_for_owner):
        raise TypeError("agent_loop_for_owner must be callable.")
    if component_registry_factory is not None and not callable(component_registry_factory):
        raise TypeError("component_registry_factory must be callable.")
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
    if prepared_oauth_provider_registry is not None:
        if not isinstance(prepared_oauth_provider_registry, PreparedOAuthProviderRegistry):
            raise TypeError("prepared_oauth_provider_registry must be a PreparedOAuthProviderRegistry")
        if oauth_authorization_service_for_provider is not None or oauth_provider_catalog is not None:
            raise ValueError("prepared OAuth registry cannot be combined with explicit OAuth resolver or catalog")
        oauth_authorization_service_for_provider = (
            prepared_oauth_provider_registry.authorization_service_for_provider
        )
        oauth_provider_catalog = prepared_oauth_provider_registry.provider_catalog
    if mcp_runtime is not None and not isinstance(mcp_runtime, MCPRuntimeService):
        raise TypeError("mcp_runtime must be an MCPRuntimeService")
    if evaluation_service is not None and not isinstance(evaluation_service, EvaluationService):
        raise TypeError("evaluation_service must be an EvaluationService")
    if mcp_runtime is not None and oauth_connections is None:
        raise ValueError("MCP management requires the OAuth connection registry")
    if (oauth_connections is None) != (oauth_disconnect_service_for_provider is None):
        raise ValueError("oauth_connections and disconnect service resolver must be configured together")
    if oauth_authorization_service_for_provider is not None and oauth_connections is None:
        raise ValueError("OAuth authorization requires the OAuth connection registry")
    if oauth_authorization_service_for_provider is not None and not callable(oauth_authorization_service_for_provider):
        raise TypeError("oauth_authorization_service_for_provider must be callable")
    if oauth_provider_catalog is not None and not callable(oauth_provider_catalog):
        raise TypeError("oauth_provider_catalog must be callable")
    if oauth_provider_catalog is not None and oauth_authorization_service_for_provider is None:
        raise ValueError("OAuth provider catalog requires authorization service configuration")

    application = build_conversation_application_runtime(
        config, agent_loop=agent_loop, agent_loop_for_owner=agent_loop_for_owner,
    )
    try:
        app = YukiConversationAPI(
            auth_service, application.conversations, tools_for_user=tools_for_user,
            provider_catalog=provider_catalog, provider_selector=provider_selector,
        )
        if evaluation_service is not None:
            app = AuthenticatedEvaluationAPI(app, auth_service, evaluation_service)
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
                    provider_catalog=oauth_provider_catalog,
                )
        if mcp_runtime is not None:
            app = AuthenticatedMCPManagementAPI(
                app, auth_service, oauth_connections, mcp_runtime,
            )
        component_registry = None
        if component_registry_factory is not None:
            component_registry = component_registry_factory(auth_service, agent_loop, application)
            if not isinstance(component_registry, ComponentRegistry):
                raise TypeError("component_registry_factory must return a ComponentRegistry.")
            # The API composition root owns the supplied configuration snapshot.
            # Preserve legacy partial test/embedding configs, but register the
            # configuration component whenever the validated runtime config is complete.
            if (
                isinstance(config.get("workspace"), str)
                and isinstance(config.get("provider"), str)
            ):
                try:
                    component_registry.resolve("configuration-runtime")
                except LookupError:
                    component_registry.register(
                        build_configuration_component_runtime(config)
                    )
            expected_instances = {
                "identity-access": auth_service,
                "agent-execution": agent_loop,
                "durable-conversations": application.conversations,
                "application-composition": application,
            }
            for component_id, expected in expected_instances.items():
                if component_registry.resolve(component_id) is not expected:
                    raise ValueError(
                        f"component registry instance mismatch: {component_id}"
                    )
            component_registry.start_all()
        return ConversationRuntime(app, application, component_registry)
    except Exception:
        if "component_registry" in locals() and isinstance(component_registry, ComponentRegistry):
            component_registry.close()
        application.close()
        raise
