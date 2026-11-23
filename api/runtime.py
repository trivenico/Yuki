"""Composable ASGI runtime for authenticated Yuki conversations."""
from __future__ import annotations

from typing import Any, Callable
from threading import RLock

from api.conversations import YukiConversationAPI
from api.income import AuthenticatedIncomeOpportunityAPI
from api.inference import AuthenticatedInferenceAPI
from api.evaluations import AuthenticatedEvaluationAPI
from api.mcp_management import AuthenticatedMCPManagementAPI
from api.oauth_connections import AuthenticatedOAuthConnectionAPI
from api.oauth_authorization import AuthenticatedOAuthAuthorizationAPI
from api.web import YukiWebApplication
from api.worker_health import AuthenticatedWorkerHealthAPI
from api.task_artifacts import AuthenticatedTaskArtifactAPI
from automation.worker_registry import WorkerRegistry
from automation.task_runtime import TaskRuntime
from core.agent_execution_loop import AgentExecutionLoop
from core.auth import BuiltInAuthService
from core.conversation_application_runtime import (
    ConversationApplicationRuntime,
    build_conversation_application_runtime,
)
from core.device_pairing import DevicePairingService
from core.income_opportunities import SQLiteIncomeOpportunityStore
from core.inference_jobs import InferenceJobService
from core.token_usage import TokenUsageTracker
from core.oauth_connection_registry import SQLiteOAuthConnectionRegistry
from core.oauth_provider_registry import PreparedOAuthProviderRegistry
from core.mcp_runtime import MCPRuntimeService
from evaluation.service import EvaluationService
from core.learning import LearningCoordinator
from core.proactive_preferences import ProactiveNotificationPreferences


class ConversationRuntime:
    """Own the authenticated API and shared conversation application lifecycle."""

    def __init__(self, app: Any, application: ConversationApplicationRuntime):
        self.app = app
        self.web = YukiWebApplication(app)
        self.application = application
        self.store = application.store
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
        self.application.close()
        self._closed = True


def build_conversation_runtime(
    config: dict[str, Any],
    *,
    auth_service: BuiltInAuthService,
    agent_loop: AgentExecutionLoop,
    agent_loop_for_owner: Callable[[str], AgentExecutionLoop] | None = None,
    memory_for_owner: Callable[[str], Any] | None = None,
    awareness_for_owner: Callable[[str, str, tuple], Any] | None = None,
    learning_for_user: Callable[[Any], LearningCoordinator] | None = None,
    proactive_preferences_for_user: Callable[[Any], ProactiveNotificationPreferences] | None = None,
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
    worker_registry: WorkerRegistry | None = None,
    task_runtime: TaskRuntime | None = None,
    usage_tracker: TokenUsageTracker | None = None,
    usage_tracker_for_owner: Callable[[str], TokenUsageTracker] | None = None,
) -> ConversationRuntime:
    """Build authenticated APIs around the shared conversation application core.

    Provider/auth construction belongs to the caller. API owner identity is
    always derived by the authenticated API from its session, never from config.
    Usage trackers are injected by the embedding application and remain scoped
    to its chosen per-agent/per-owner ledger paths.
    """
    if not isinstance(auth_service, BuiltInAuthService):
        raise TypeError("auth_service must be a BuiltInAuthService.")
    if not isinstance(agent_loop, AgentExecutionLoop):
        raise TypeError("agent_loop must be an AgentExecutionLoop.")
    if usage_tracker is not None and not isinstance(usage_tracker, TokenUsageTracker):
        raise TypeError("usage_tracker must be a TokenUsageTracker.")
    if usage_tracker_for_owner is not None and not callable(usage_tracker_for_owner):
        raise TypeError("usage_tracker_for_owner must be callable.")
    if learning_for_user is not None and not callable(learning_for_user):
        raise TypeError("learning_for_user must be callable.")
    if proactive_preferences_for_user is not None and not callable(proactive_preferences_for_user):
        raise TypeError("proactive_preferences_for_user must be callable.")
    if usage_tracker_for_owner is not None and agent_loop_for_owner is None:
        raise ValueError("usage_tracker_for_owner requires agent_loop_for_owner.")
    if usage_tracker is not None:
        previous_usage_callback = agent_loop.on_usage
        def record_default_usage(usage):
            if previous_usage_callback is not None:
                previous_usage_callback(usage)
            usage_tracker.record_provider_usage(usage)
        agent_loop.on_usage = record_default_usage
    if usage_tracker_for_owner is not None:
        original_owner_factory = agent_loop_for_owner
        owner_trackers = {}
        owner_tracker_lock = RLock()
        def tracked_owner_factory(owner_id):
            owner_loop = original_owner_factory(owner_id)
            if not isinstance(owner_loop, AgentExecutionLoop):
                raise TypeError("agent_loop_for_owner must return an AgentExecutionLoop.")
            with owner_tracker_lock:
                owner_tracker = owner_trackers.get(owner_id)
                if owner_tracker is None:
                    owner_tracker = usage_tracker_for_owner(owner_id)
                    if not isinstance(owner_tracker, TokenUsageTracker):
                        raise TypeError("usage_tracker_for_owner must return a TokenUsageTracker.")
                    owner_trackers[owner_id] = owner_tracker
            if getattr(owner_loop, "_yuki_usage_tracker", None) is not owner_tracker:
                previous_callback = owner_loop.on_usage
                def record_owner_usage(usage):
                    if previous_callback is not None:
                        previous_callback(usage)
                    owner_tracker.record_provider_usage(usage)
                owner_loop.on_usage = record_owner_usage
                owner_loop._yuki_usage_tracker = owner_tracker
            return owner_loop
        agent_loop_for_owner = tracked_owner_factory
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
    if worker_registry is not None and not isinstance(worker_registry, WorkerRegistry):
        raise TypeError("worker_registry must be a WorkerRegistry")
    if task_runtime is not None and not isinstance(task_runtime, TaskRuntime):
        raise TypeError("task_runtime must be a TaskRuntime")
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
        memory_for_owner=memory_for_owner,
        awareness_for_owner=awareness_for_owner,
    )
    try:
        app = YukiConversationAPI(
            auth_service, application.conversations, tools_for_user=tools_for_user,
            provider_catalog=provider_catalog, provider_selector=provider_selector,
            learning_for_user=learning_for_user,
            proactive_preferences_for_user=proactive_preferences_for_user,
        )
        if evaluation_service is not None:
            app = AuthenticatedEvaluationAPI(app, auth_service, evaluation_service)
        if worker_registry is not None:
            app = AuthenticatedWorkerHealthAPI(app, auth_service, worker_registry)
        if task_runtime is not None:
            app = AuthenticatedTaskArtifactAPI(app, auth_service, task_runtime)
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
        return ConversationRuntime(app, application)
    except Exception:
        application.close()
        raise
