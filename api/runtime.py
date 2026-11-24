"""Composable ASGI runtime for authenticated Yuki conversations."""
from __future__ import annotations

from typing import Any, Callable
import hashlib
import json

from api.conversations import YukiConversationAPI
from api.income import AuthenticatedIncomeOpportunityAPI
from api.inference import AuthenticatedInferenceAPI
from api.evaluations import AuthenticatedEvaluationAPI
from api.mcp_management import AuthenticatedMCPManagementAPI
from api.oauth_connections import AuthenticatedOAuthConnectionAPI
from api.oauth_authorization import AuthenticatedOAuthAuthorizationAPI
from api.web import YukiWebApplication
from core.agent_execution_loop import AgentExecutionLoop
from core.authorization import AuthorizationRequest, Principal
from core.authorization_gateway import AuthorizationGateway
from core.auth import BuiltInAuthService
from core.conversation_application_runtime import (
    ConversationApplicationRuntime,
    build_conversation_application_runtime,
)
from core.device_pairing import DevicePairingService
from core.income_opportunities import SQLiteIncomeOpportunityStore
from core.inference_jobs import InferenceJobService
from core.oauth_connection_registry import SQLiteOAuthConnectionRegistry
from core.oauth_provider_registry import PreparedOAuthProviderRegistry
from core.mcp_runtime import MCPRuntimeService
from core.production_security import ProductionSecurityContext
from evaluation.service import EvaluationService
from providers.authorized_agent_adapter import AuthorizedProviderAgentAdapter


class ConversationRuntime:
    """Own the authenticated API and shared conversation application lifecycle."""

    def __init__(
        self,
        app: Any,
        application: ConversationApplicationRuntime,
        *,
        production_security: ProductionSecurityContext | None = None,
        production_security_runtime: Any | None = None,
        production_worker_runtime: Any | None = None,
    ):
        self.app = app
        self.web = YukiWebApplication(app)
        self.application = application
        if production_security_runtime is not None:
            if getattr(production_security_runtime, "security", None) is not production_security:
                raise ValueError("production_security_runtime_context_mismatch")
            if not callable(getattr(production_security_runtime, "verify_readiness", None)) or not callable(getattr(production_security_runtime, "close", None)):
                raise TypeError("production_security_runtime_requires_readiness_and_close")
        if production_worker_runtime is not None:
            if production_security_runtime is None:
                raise ValueError("production_worker_runtime_requires_owned_security_runtime")
            if not callable(getattr(production_worker_runtime, "verify_readiness", None)) or not callable(getattr(production_worker_runtime, "close", None)):
                raise TypeError("production_worker_runtime_requires_readiness_and_close")
        self.production_security = production_security
        self.production_security_runtime = production_security_runtime
        self.production_worker_runtime = production_worker_runtime
        self.store = application.store
        self._closed = False

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            if (
                self.production_security is not None
                and scope.get("method") == "GET"
                and scope.get("path") == "/health/ready"
            ):
                try:
                    if self.production_security_runtime is not None:
                        self.production_security_runtime.verify_readiness()
                        checks = {"production_security": "ok"}
                    else:
                        self.production_security.verify_audit_integrity()
                        checks = {"authorization_audit": "ok"}
                    if self.production_worker_runtime is not None:
                        self.production_worker_runtime.verify_readiness()
                        checks["production_worker"] = "ok"
                    status = 200
                    payload = {"status": "ready", "checks": checks }
                except Exception:
                    status = 503
                    payload = {"status": "not_ready", "checks": {"production_runtime": "failed"}}
                body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
                await send({
                    "type": "http.response.start",
                    "status": status,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode("ascii")),
                        (b"cache-control", b"no-store"),
                    ],
                })
                await send({"type": "http.response.body", "body": body})
                return
            await self.web(scope, receive, send)
            return
        if scope.get("type") != "lifespan":
            raise RuntimeError("ConversationRuntime supports HTTP and ASGI lifespan scopes.")
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                try:
                    if self.production_security_runtime is not None:
                        self.production_security_runtime.verify_readiness()
                    if self.production_worker_runtime is not None:
                        self.production_worker_runtime.verify_readiness()
                except Exception:
                    try:
                        self.close()
                    except Exception:
                        pass
                    await send({
                        "type": "lifespan.startup.failed",
                        "message": "production_runtime_not_ready",
                    })
                    return
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                self.close()
                await send({"type": "lifespan.shutdown.complete"})
                return

    def close(self) -> None:
        """Close backend resources once during graceful application shutdown."""
        if self._closed:
            return
        errors = []
        try:
            self.application.close()
        except Exception as exc:
            errors.append(exc)
        if self.production_worker_runtime is not None:
            try:
                self.production_worker_runtime.close()
            except Exception as exc:
                errors.append(exc)
        if self.production_security_runtime is not None:
            try:
                self.production_security_runtime.close()
            except Exception as exc:
                errors.append(exc)
        self._closed = True
        if errors:
            raise RuntimeError("conversation_runtime_shutdown_failed") from errors[0]


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
    income_opportunities: SQLiteIncomeOpportunityStore | None = None,
    oauth_connections: SQLiteOAuthConnectionRegistry | None = None,
    oauth_disconnect_service_for_provider: Callable[[str], Any] | None = None,
    oauth_authorization_service_for_provider: Callable[[str], Any] | None = None,
    oauth_provider_catalog: Callable[[], list[dict[str, Any]]] | None = None,
    prepared_oauth_provider_registry: PreparedOAuthProviderRegistry | None = None,
    mcp_runtime: MCPRuntimeService | None = None,
    authorization_gateway: AuthorizationGateway | None = None,
    production_security: ProductionSecurityContext | None = None,
    production_security_runtime: Any | None = None,
    production_worker_runtime: Any | None = None,
    require_central_authorization: bool = False,
    evaluation_service: EvaluationService | None = None,
) -> ConversationRuntime:
    """Build authenticated APIs around the shared conversation application core.

    Provider/auth construction belongs to the caller. API owner identity is
    always derived by the authenticated API from its session, never from config.

    When require_central_authorization is enabled, every conversation execution
    must obtain an owner-scoped AgentExecutionLoop whose GuardedToolRuntime uses
    the exact shared gateway and a Principal matching the authenticated owner.
    Configured MCP execution and every owner-scoped direct model-provider adapter
    must use that same gateway instance and authenticated owner principal.
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
    if type(require_central_authorization) is not bool:
        raise TypeError("require_central_authorization must be a boolean")
    if authorization_gateway is not None and not isinstance(authorization_gateway, AuthorizationGateway):
        raise TypeError("authorization_gateway must be an AuthorizationGateway")
    if production_security is not None and not isinstance(production_security, ProductionSecurityContext):
        raise TypeError("production_security must be a ProductionSecurityContext")
    if production_worker_runtime is not None:
        if production_security_runtime is None:
            raise ValueError("production_worker_runtime_requires_owned_security_runtime")
        if not callable(getattr(production_worker_runtime, "verify_readiness", None)) or not callable(getattr(production_worker_runtime, "close", None)):
            raise TypeError("production_worker_runtime_requires_readiness_and_close")
    if production_security_runtime is not None:
        runtime_security = getattr(production_security_runtime, "security", None)
        if not isinstance(runtime_security, ProductionSecurityContext):
            raise TypeError("production_security_runtime must expose a ProductionSecurityContext")
        if production_security is not None and production_security is not runtime_security:
            raise ValueError("production_security_runtime_context_mismatch")
        production_security = runtime_security
        if not callable(getattr(production_security_runtime, "verify_readiness", None)) or not callable(getattr(production_security_runtime, "close", None)):
            raise TypeError("production_security_runtime_requires_readiness_and_close")
    if mcp_runtime is not None and mcp_runtime.central_authorization_enabled:
        if authorization_gateway is not None and mcp_runtime.authorization_gateway is not authorization_gateway:
            raise ValueError("mcp_runtime_must_use_shared_authorization_gateway")
    if production_worker_runtime is not None and not require_central_authorization:
        raise ValueError("production_worker_runtime_requires_central_authorization")
    if require_central_authorization:
        if production_security is None:
            raise ValueError("production_runtime_requires_security_context")
        if authorization_gateway is None:
            authorization_gateway = production_security.authorization_gateway
        if authorization_gateway is not production_security.authorization_gateway:
            raise ValueError("production_runtime_security_context_gateway_mismatch")
        if agent_loop_for_owner is None:
            raise ValueError("production_runtime_requires_owner_scoped_authorized_agent_loops")
        if mcp_runtime is not None and mcp_runtime.authorization_gateway is not authorization_gateway:
            raise ValueError("production_mcp_runtime_must_use_shared_authorization_gateway")

        if not callable(agent_loop_for_owner):
            raise TypeError("agent_loop_for_owner must be callable")

        trusted_loop_factory = agent_loop_for_owner

        def agent_loop_for_owner(owner_id: str) -> AgentExecutionLoop:
            loop = trusted_loop_factory(owner_id)
            if not isinstance(loop, AgentExecutionLoop):
                raise TypeError("agent_loop_for_owner must return an AgentExecutionLoop")
            guarded = loop.guarded_tool_runtime
            if guarded.authorization_gateway is not authorization_gateway:
                raise ValueError("owner_agent_loop_not_bound_to_shared_authorization_gateway")
            principal = guarded.principal
            expected_principal = production_security.require_actor(owner_id)
            if not isinstance(principal, Principal) or principal != expected_principal:
                raise ValueError("owner_agent_loop_principal_mismatch")
            if guarded.policy_revision != production_security.policy_revision:
                raise ValueError("owner_agent_loop_policy_revision_mismatch")
            provider_adapter = loop.model
            if not isinstance(provider_adapter, AuthorizedProviderAgentAdapter):
                raise ValueError("owner_agent_loop_requires_authorized_provider_adapter")
            if provider_adapter.authorization_gateway is not authorization_gateway:
                raise ValueError("owner_provider_adapter_not_bound_to_shared_authorization_gateway")
            if provider_adapter.principal != principal:
                raise ValueError("owner_provider_adapter_principal_mismatch")
            if provider_adapter.policy_revision != production_security.policy_revision:
                raise ValueError("owner_provider_adapter_policy_revision_mismatch")
            if loop.stream_model is not None and (
                getattr(loop.stream_model, "__self__", None) is not provider_adapter
                or getattr(loop.stream_model, "__func__", None)
                is not AuthorizedProviderAgentAdapter.stream_model
            ):
                raise ValueError("owner_stream_model_not_bound_to_authorized_provider_adapter")
            return loop
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
        provider_selection_authorizer = None
        if require_central_authorization and provider_selector is not None:
            def provider_selection_authorizer(user, provider_name: str) -> None:
                principal = production_security.require_actor(user.user_id)
                fingerprint = hashlib.sha256(provider_name.encode('utf-8')).hexdigest()
                authorization_gateway.require(AuthorizationRequest(
                    principal=principal,
                    action='inference:provider:select',
                    resource='provider-selection:sha256:' + fingerprint,
                    risk='high',
                    expected_policy_revision=production_security.policy_revision,
                ))
        app = YukiConversationAPI(
            auth_service, application.conversations, tools_for_user=tools_for_user,
            provider_catalog=provider_catalog, provider_selector=provider_selector,
            provider_selection_authorizer=provider_selection_authorizer,
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
                authorization_gateway=(
                    production_security.authorization_gateway
                    if require_central_authorization else None
                ),
                principal_for_actor=(
                    production_security.require_actor
                    if require_central_authorization else None
                ),
                policy_revision=(
                    production_security.policy_revision
                    if require_central_authorization else None
                ),
            )
        return ConversationRuntime(
            app, application,
            production_security=(production_security if require_central_authorization else None),
            production_security_runtime=(production_security_runtime if require_central_authorization else None),
            production_worker_runtime=(production_worker_runtime if require_central_authorization else None),
        )
    except Exception:
        application.close()
        raise
