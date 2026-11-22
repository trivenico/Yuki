"""Composable ASGI runtime for authenticated Yuki conversations."""
from __future__ import annotations

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
from core.conversation_application_runtime import (
    ConversationApplicationRuntime,
    build_conversation_application_runtime,
)
from core.device_pairing import DevicePairingService
from core.device_agent import DeviceAgentService
from core.device_resource_reservations import DeviceResourceReservationService
from core.income_opportunities import SQLiteIncomeOpportunityStore
from core.inference_jobs import InferenceJobService
from core.oauth_connection_registry import SQLiteOAuthConnectionRegistry
from core.oauth_provider_registry import PreparedOAuthProviderRegistry
from core.mcp_runtime import MCPRuntimeService
from evaluation.service import EvaluationService


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
    tools_for_user: Callable[[Any], list[dict[str, Any]]] | None = None,
    provider_catalog: Callable[[], list[dict[str, Any]]] | None = None,
    provider_selector: Callable[[str], dict[str, Any]] | None = None,
    device_pairing: DevicePairingService | None = None,
    inference_jobs: InferenceJobService | None = None,
    device_agent: DeviceAgentService | None = None,
    income_opportunities: SQLiteIncomeOpportunityStore | None = None,
    oauth_connections: SQLiteOAuthConnectionRegistry | None = None,
    oauth_disconnect_service_for_provider: Callable[[str], Any] | None = None,
    oauth_authorization_service_for_provider: Callable[[str], Any] | None = None,
    oauth_provider_catalog: Callable[[], list[dict[str, Any]]] | None = None,
    prepared_oauth_provider_registry: PreparedOAuthProviderRegistry | None = None,
    mcp_runtime: MCPRuntimeService | None = None,
    evaluation_service: EvaluationService | None = None,
) -> ConversationRuntime:
    """Build authenticated APIs around the shared conversation application core.

    Provider/auth construction belongs to the caller. API owner identity is
    always derived by the authenticated API from its session, never from config.
    """
    if not isinstance(auth_service, BuiltInAuthService):
        raise TypeError("auth_service must be a BuiltInAuthService.")
    if not isinstance(agent_loop, AgentExecutionLoop):
        raise TypeError("agent_loop must be an AgentExecutionLoop.")
    if (device_pairing is None) != (inference_jobs is None):
        raise ValueError("device_pairing and inference_jobs must be configured together")
    if device_pairing is not None and inference_jobs.pairing is not device_pairing:
        raise ValueError("inference_jobs must use the configured device_pairing service")
    if inference_jobs is not None and inference_jobs.reservation_service is not None:
        reservations = inference_jobs.reservation_service
        if not isinstance(reservations, DeviceResourceReservationService):
            raise TypeError("inference_jobs.reservation_service must be a DeviceResourceReservationService")
        if reservations.pairing_service is not device_pairing:
            raise ValueError("inference_jobs reservation service must use the configured device_pairing service")
        if reservations.auth_service is not auth_service:
            raise ValueError("inference_jobs reservation service must use the configured auth_service")
    if device_agent is not None:
        if not isinstance(device_agent, DeviceAgentService):
            raise TypeError("device_agent must be a DeviceAgentService")
        if device_pairing is None:
            raise ValueError("device_agent requires device_pairing and inference_jobs")
        if device_agent.pairing is not device_pairing:
            raise ValueError("device_agent must use the configured device_pairing service")
        reservations = device_agent.reservation_service
        if not isinstance(reservations, DeviceResourceReservationService):
            raise ValueError("device_agent requires a DeviceResourceReservationService")
        if reservations.pairing_service is not device_pairing:
            raise ValueError("device reservations must use the configured device_pairing service")
        if reservations.auth_service is not auth_service:
            raise ValueError("device reservations must use the configured auth_service")
        if inference_jobs.reservation_service is not reservations:
            raise ValueError("inference_jobs must use the configured device reservation service")
        if device_pairing.database_path != inference_jobs.database_path:
            raise ValueError("device_pairing_and_inference_jobs_must_share_database")
        device_agent.inference_jobs = inference_jobs
        # Finish durable device revocations before accepting worker traffic.
        revocation_recovery = device_agent.reconcile_device_revocations()
        if revocation_recovery["deferred"]:
            raise RuntimeError("pending_device_revocations_could_not_be_reconciled")
    # Without a DeviceAgentService there is no component capable of
    # completing durable device-revocation sagas. Refuse startup if any are
    # pending rather than serving a runtime that cannot resolve them.
    if (device_agent is None and device_pairing is not None
            and device_pairing.pending_device_revocations(limit=1000)):
        raise RuntimeError("pending_device_revocations_require_device_agent")
    if (inference_jobs is not None
            and inference_jobs.reservation_service is None
            and inference_jobs.has_reservation_state()):
        # A config downgrade must not accept traffic while queued/running jobs
        # or pending sagas still depend on the separate reservation ledger.
        raise RuntimeError("reservation_service_required_for_persisted_jobs")
    if inference_jobs is not None and inference_jobs.reservation_service is not None:
        # Persist lease expiry for the entire ledger at startup, not only when
        # a device next requests capacity. Drain bounded batches so large
        # ledgers do not retain elapsed rows as active indefinitely.
        while True:
            expired_reservations = inference_jobs.reservation_service.expire_stale_reservations(
                limit=10_000,
            )
            if expired_reservations < 10_000:
                break
        # Expired worker claims are durable completion sagas too. Journal and
        # reconcile them before the generic reservation-operation backlog.
        lease_recovery = inference_jobs.recover_expired_worker_leases()
        if lease_recovery["deferred"]:
            raise RuntimeError("expired_worker_leases_could_not_be_reconciled")
        # Recover every stale reservation operation before accepting worker traffic.
        # The reconciler is deliberately bounded; drain full batches so a backlog
        # larger than one page cannot survive startup unnoticed.
        while True:
            reservation_recovery = inference_jobs.reconcile_reservation_operations(
                limit=1000,
            )
            if reservation_recovery["deferred"]:
                raise RuntimeError("pending_reservation_operations_could_not_be_reconciled")
            if reservation_recovery["completed"] + reservation_recovery["abandoned"] < 1000:
                break
        # Fresh intents may belong to requests running in another process
        # during a rolling restart. Keep the stale cutoff: reconciliation will
        # recover abandoned work after its grace period. Job eligibility remains
        # fail-closed (unbound jobs cannot be claimed; pending cancellations are
        # excluded by the claim query), so a live intent need not block startup.
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
            app = AuthenticatedInferenceAPI(
                app, auth_service, device_pairing, inference_jobs, device_agent=device_agent,
            )
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
