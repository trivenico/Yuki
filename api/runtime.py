"""Composable ASGI runtime for authenticated Yuki conversations."""
from __future__ import annotations

import json
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
from core.owner_agent_component_runtime import build_owner_agent_execution_component_runtime
from core.conversation_memory_store import ConversationMemoryStore
from core.conversation_application_runtime import (
    ConversationApplicationRuntime,
    build_conversation_application_runtime,
)
from core.device_pairing import DevicePairingService
from core.readiness import ReadinessReport, build_readiness_report
from core.network_firewall import NetworkFirewall, build_network_firewall_from_config
from core.network_firewall_component_runtime import build_network_firewall_component_runtime
from core.income_opportunities import SQLiteIncomeOpportunityStore
from core.inference_jobs import InferenceJobService
from core.oauth_connection_registry import SQLiteOAuthConnectionRegistry
from core.oauth_provider_registry import PreparedOAuthProviderRegistry
from core.mcp_runtime import MCPRuntimeService
from evaluation.service import EvaluationService
from core.tool_action_approvals import SQLiteToolActionApprovalStore
from core.tool_action_context import ToolActionContext
from core.pending_tool_action_proposals import SQLitePendingToolActionProposalStore
from core.approved_tool_action_executor import ApprovedToolActionExecutor
from core.tool_action_reconciliation import ToolActionReconciliationService
from core.google_gmail_runtime import GmailToolRuntimeBundle


class ConversationRuntime:
    """Own the authenticated API and shared conversation application lifecycle."""

    def __init__(self, app: Any, application: ConversationApplicationRuntime, component_registry: ComponentRegistry | None = None):
        self.app = app
        self.web = YukiWebApplication(app)
        self.application = application
        self.component_registry = component_registry
        self.sandbox_execution_runtime = sandbox_execution_runtime
        self.store = application.store
        self._closed = False
        self._close_lock = RLock()

    async def __call__(self, scope, receive, send):
        if self._closed:
            raise RuntimeError("conversation runtime is closed")
        if scope.get("type") == "http":
            if scope.get("path") == "/readyz" and scope.get("method", "GET").upper() in {"GET", "HEAD"}:
                # A probe must fail closed even if report construction itself
                # encounters an unexpected platform or diagnostic error.
                try:
                    report = self.readiness_report()
                    component_status = report.capabilities["component_runtime"].status
                except Exception:
                    component_status = "unavailable"
                ready = component_status == "verified"
                payload = {
                    "status": "ready" if ready else "not_ready",
                    "component_runtime": component_status,
                }
                body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
                await self.web.respond(
                    send, 200 if ready else 503, body,
                    "application/json; charset=utf-8",
                    scope.get("method", "GET").upper(),
                )
                return
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
            failures = []
            if self.component_registry is not None:
                failures.extend(self.component_registry.close())
            else:
                self.application.close()
            if self.sandbox_execution_runtime is not None:
                try:
                    self.sandbox_execution_runtime.close()
                except Exception:
                    failures.append("sandbox-execution-runtime")
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
    tool_action_approvals: SQLiteToolActionApprovalStore | None = None,
    pending_tool_action_proposals: SQLitePendingToolActionProposalStore | None = None,
    approved_tool_action_executor: ApprovedToolActionExecutor | None = None,
    tool_action_reconciler: ToolActionReconciliationService | None = None,
    gmail_runtime_bundle: GmailToolRuntimeBundle | None = None,
    pending_proposal_factory: Callable | None = None,
    action_context_factory: Callable | None = None,
    confirmation_check: Callable | None = None,
    approval_id_factory: Callable | None = None,
    agent_loop_for_owner: Callable[[str], AgentExecutionLoop] | None = None,
    component_registry_factory: Callable[[BuiltInAuthService, AgentExecutionLoop, ConversationApplicationRuntime], ComponentRegistry] | None = None,
    memory_store: ConversationMemoryStore | None = None,
    network_firewall: NetworkFirewall | None = None,
    sandbox_execution_runtime=None,
) -> ConversationRuntime:
    """Build authenticated APIs around the shared conversation application core.

    Provider/auth construction belongs to the caller. API owner identity is
    always derived by the authenticated API from its session, never from config.
    """
    if not isinstance(auth_service, BuiltInAuthService):
        raise TypeError("auth_service must be a BuiltInAuthService.")
    if not isinstance(agent_loop, AgentExecutionLoop):
        raise TypeError("agent_loop must be an AgentExecutionLoop.")
    if memory_store is not None and not isinstance(memory_store, ConversationMemoryStore):
        raise TypeError("memory_store must be a ConversationMemoryStore or None.")
    if network_firewall is not None and not isinstance(network_firewall, NetworkFirewall):
        raise TypeError("network_firewall must be a NetworkFirewall or None.")
    if network_firewall is None:
        network_firewall = build_network_firewall_from_config(config)
    if gmail_runtime_bundle is not None:
        if not isinstance(gmail_runtime_bundle, GmailToolRuntimeBundle):
            raise TypeError("gmail_runtime_bundle must be a GmailToolRuntimeBundle.")
        if agent_loop.tool_protocol is not gmail_runtime_bundle.tool_protocol:
            raise ValueError("agent_loop must use the Gmail bundle's exact structured tool protocol.")
        if tools_for_user is None:
            raise ValueError("Gmail composition requires an owner-scoped tools_for_user callback.")
        if tool_action_approvals is not None or pending_tool_action_proposals is not None:
            raise ValueError("Gmail bundle owns the durable approval and proposal stores.")
        if approved_tool_action_executor is not None or tool_action_reconciler is not None:
            raise ValueError("Gmail bundle owns the approved executor and reconciliation service.")
        tool_action_approvals = gmail_runtime_bundle.approval_store
        pending_tool_action_proposals = gmail_runtime_bundle.proposals
        approved_tool_action_executor = ApprovedToolActionExecutor(
            gmail_runtime_bundle.proposals,
            gmail_runtime_bundle.approval_store,
            gmail_runtime_bundle.guarded_runtime,
        )
        tool_action_reconciler = gmail_runtime_bundle.reconciliation_service
    if agent_loop_for_owner is not None and not callable(agent_loop_for_owner):
        raise TypeError("agent_loop_for_owner must be callable.")
    for callback, name in (
        (action_context_factory, "action_context_factory"),
        (confirmation_check, "confirmation_check"),
        (approval_id_factory, "approval_id_factory"),
        (pending_proposal_factory, "pending_proposal_factory"),
    ):
        if callback is not None and not callable(callback):
            raise TypeError(f"{name} must be callable.")
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
    if mcp_runtime is not None and network_firewall is not None:
        if not mcp_runtime.uses_firewall_policy(network_firewall):
            raise ValueError(
                "MCP connection managers must use the exact API network firewall policy"
            )
    if evaluation_service is not None and not isinstance(evaluation_service, EvaluationService):
        raise TypeError("evaluation_service must be an EvaluationService")
    if tool_action_approvals is not None and not isinstance(
        tool_action_approvals, SQLiteToolActionApprovalStore
    ):
        raise TypeError("tool_action_approvals must be a SQLiteToolActionApprovalStore")
    if pending_tool_action_proposals is not None and not isinstance(
        pending_tool_action_proposals, SQLitePendingToolActionProposalStore
    ):
        raise TypeError("pending_tool_action_proposals must be a SQLitePendingToolActionProposalStore")
    if approved_tool_action_executor is not None:
        if not isinstance(approved_tool_action_executor, ApprovedToolActionExecutor):
            raise TypeError("approved_tool_action_executor must be an ApprovedToolActionExecutor")
        if pending_tool_action_proposals is None or approved_tool_action_executor.proposals is not pending_tool_action_proposals:
            raise ValueError("executor must use the configured pending proposal store")
        if tool_action_approvals is None or approved_tool_action_executor.approvals is not tool_action_approvals:
            raise ValueError("executor must use the configured approval store")
    if tool_action_reconciler is not None:
        if not isinstance(tool_action_reconciler, ToolActionReconciliationService):
            raise TypeError("tool_action_reconciler must be a ToolActionReconciliationService")
        if pending_tool_action_proposals is None or tool_action_reconciler.proposals is not pending_tool_action_proposals:
            raise ValueError("reconciler must use the configured pending proposal store")
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

    # When durable approvals are configured, provide safe default host adapters.
    # The confirmation decision remains an explicit embedding/UI responsibility;
    # AgentExecutionLoop defaults it to False when no checker is supplied.
    if pending_tool_action_proposals is not None:
        if action_context_factory is None:
            action_context_factory = (
                lambda principal_id, tool_name, arguments:
                ToolActionContext.create(principal_id, tool_name, arguments)
                if principal_id is not None else None
            )
        if pending_proposal_factory is None:
            def pending_proposal_factory(principal_id, action_context, call):
                if principal_id is None or action_context is None:
                    raise ValueError("authenticated proposal identity is required")
                proposal = pending_tool_action_proposals.create(
                    action_context, call.arguments, principal_id=principal_id,
                )
                return proposal.proposal_id

    if tool_action_approvals is not None:
        if action_context_factory is None:
            action_context_factory = (
                lambda principal_id, tool_name, arguments:
                ToolActionContext.create(principal_id, tool_name, arguments)
                if principal_id is not None else None
            )
        if approval_id_factory is None:
            def approval_id_factory(principal_id, action_context, call):
                if principal_id is None or action_context is None:
                    return None
                return tool_action_approvals.issue(
                    action_context, principal_id=principal_id,
                )

    application = build_conversation_application_runtime(
        config, agent_loop=agent_loop, agent_loop_for_owner=agent_loop_for_owner,
        action_context_factory=action_context_factory,
        confirmation_check=confirmation_check,
        approval_id_factory=approval_id_factory,
        pending_proposal_factory=pending_proposal_factory,
        memory_store=memory_store,
    )
    try:
        app = YukiConversationAPI(
            auth_service, application.conversations, tools_for_user=tools_for_user,
            provider_catalog=provider_catalog, provider_selector=provider_selector,
            tool_action_approvals=tool_action_approvals,
            pending_tool_action_proposals=pending_tool_action_proposals,
            approved_tool_action_executor=approved_tool_action_executor,
            tool_action_reconciler=tool_action_reconciler,
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
            owner_factory = application.conversations.agent_loop_for_owner
            if owner_factory is not None:
                try:
                    registered_owner_factory = component_registry.resolve("owner-agent-execution")
                except LookupError:
                    component_registry.register(
                        build_owner_agent_execution_component_runtime(owner_factory)
                    )
                else:
                    if registered_owner_factory is not owner_factory:
                        raise ValueError(
                            "component registry owner-agent-execution factory does not match API runtime"
                        )
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
            if network_firewall is not None:
                try:
                    registered_firewall = component_registry.resolve("network-firewall")
                except LookupError:
                    component_registry.register(
                        build_network_firewall_component_runtime(network_firewall)
                    )
                else:
                    if registered_firewall is not network_firewall:
                        raise ValueError("component registry network-firewall instance mismatch")
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
