import tempfile
import unittest
from pathlib import Path

from api.mcp_management import AuthenticatedMCPManagementAPI
from api.runtime import build_conversation_runtime as _build_conversation_runtime
from core.agent_execution_loop import AgentExecutionLoop
from core.approval_audit import SQLiteApprovalAuditLog
from core.auth import BuiltInAuthService
from core.guarded_tool_runtime import GuardedToolRuntime
from core.mcp_runtime import MCPProviderConnectionConfig, MCPRuntimeService
from core.mcp_tool_policy_store import SQLiteMCPToolPolicyStore
from core.oauth_connection_registry import SQLiteOAuthConnectionRegistry
from core.oauth_mcp_connection import OAuthMCPConnectionManager
from core.structured_tool_protocol import StructuredToolProtocol
from core.authorization import AuthorizationDecision, Principal
from core.authorization_gateway import AuthorizationGateway
from core.authorization_audit import SQLiteAuthorizationAuditLog
from providers.authorized_agent_adapter import AuthorizedProviderAgentAdapter
from core.production_security import ProductionSecurityContext


class NoApprovals:
    def authorize_tool(self, _name):
        return False


class FakeVault:
    def get(self, **_kwargs):
        return None


class FakeRefresh:
    def refresh_if_needed(self, **_kwargs):
        return {}

class AllowMCPPolicy:
    def authorize(self, request):
        return AuthorizationDecision(True, "decision-1", "1", "allowed")




class _MemoryAuditAnchor:
    def __init__(self):
        self.checkpoint = None
    def publish(self, checkpoint):
        self.checkpoint = checkpoint
    def latest(self):
        return self.checkpoint


def _gateway():
    audit_log = SQLiteAuthorizationAuditLog(str(Path(tempfile.mkdtemp()) / "authorization-audit.sqlite3"))
    return AuthorizationGateway(AllowMCPPolicy(), audit=audit_log)


def build_conversation_runtime(*args, **kwargs):
    if kwargs.get('require_central_authorization') is True and kwargs.get('authorization_gateway') is not None:
        gateway = kwargs['authorization_gateway']
        anchor = _MemoryAuditAnchor()
        gateway.audit_writer.publish_checkpoint(anchor)
        principal_factory = kwargs.pop('_security_principal_factory', lambda owner: Principal(owner, 'human'))
        kwargs['production_security'] = ProductionSecurityContext(
            authorization_gateway=gateway,
            principal_for_actor=principal_factory,
            principal_for_worker=lambda worker: Principal(worker, 'workload', tenant_id='test-tenant', agent_id='test-agent'),
            policy_revision='1',
            audit_anchor=anchor,
        )
    return _build_conversation_runtime(*args, **kwargs)

class MCPRuntimeWiringTests(unittest.TestCase):
    def _loop(self, gateway=None, owner=None):
        def model(**_kwargs):
            return {"text": "ready", "tool_calls": {"tool_calls": []}}
        if gateway is None:
            guarded = GuardedToolRuntime({}, NoApprovals())
        else:
            guarded = GuardedToolRuntime(
                {}, gateway, principal=Principal(owner, "human"), policy_revision="1",
            )
        protocol = StructuredToolProtocol(guarded)
        return AgentExecutionLoop(model, protocol)

    def _mcp_runtime(self, root, gateway=None):
        manager = OAuthMCPConnectionManager(
            provider_id="provider", credential_vault=FakeVault(),
            refresh_service=FakeRefresh(), client_factory=lambda *_args, **_kwargs: None,
        )
        return MCPRuntimeService(
            connection_managers={"provider": manager},
            provider_configs={"provider": MCPProviderConnectionConfig(
                endpoint="https://mcp.example/mcp", allowed_hosts=("mcp.example",),
                required_scopes=("profile:read",),
            )},
            policy_store=SQLiteMCPToolPolicyStore(str(root / "policies.sqlite3")),
            audit_log=SQLiteApprovalAuditLog(str(root / "audit.sqlite3")),
            authorization_gateway=gateway,
            principal_for_owner=(lambda owner: Principal(owner, "human")) if gateway is not None else None,
        )

    def test_runtime_mounts_mcp_management_only_with_oauth_registry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            auth = BuiltInAuthService(str(root / "auth.sqlite3"), password_iterations=100_000)
            oauth = SQLiteOAuthConnectionRegistry(str(root / "oauth.sqlite3"))
            runtime = build_conversation_runtime(
                {"storage_backend": "sqlite", "workspace": directory},
                auth_service=auth, agent_loop=self._loop(),
                oauth_connections=oauth,
                oauth_disconnect_service_for_provider=lambda _provider: None,
                mcp_runtime=self._mcp_runtime(root),
            )
            self.assertIsInstance(runtime.app, AuthenticatedMCPManagementAPI)
            self.assertIs(runtime.app.oauth_registry, oauth)
            runtime.close()

    def test_runtime_rejects_mcp_management_without_oauth_registry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            auth = BuiltInAuthService(str(root / "auth.sqlite3"), password_iterations=100_000)
            with self.assertRaisesRegex(ValueError, "requires the OAuth connection registry"):
                build_conversation_runtime(
                    {"storage_backend": "sqlite", "workspace": directory},
                    auth_service=auth, agent_loop=self._loop(),
                    mcp_runtime=self._mcp_runtime(root),
                )

    def test_production_runtime_rejects_mcp_without_central_gateway(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            auth = BuiltInAuthService(str(root / "auth.sqlite3"), password_iterations=100_000)
            oauth = SQLiteOAuthConnectionRegistry(str(root / "oauth.sqlite3"))
            with self.assertRaisesRegex(ValueError, "production_runtime_requires_security_context"):
                build_conversation_runtime(
                    {"storage_backend": "sqlite", "workspace": directory},
                    auth_service=auth, agent_loop=self._loop(),
                    oauth_connections=oauth,
                    oauth_disconnect_service_for_provider=lambda _provider: None,
                    mcp_runtime=self._mcp_runtime(root),
                    require_central_authorization=True,
                )


    def test_production_runtime_rejects_owner_loop_without_authorized_provider_adapter(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            auth = BuiltInAuthService(str(root / "auth.sqlite3"), password_iterations=100_000)
            gateway = _gateway()
            with self.assertRaisesRegex(ValueError, "requires_authorized_provider_adapter"):
                build_conversation_runtime(
                    {"storage_backend": "sqlite", "workspace": directory},
                    auth_service=auth, agent_loop=self._loop(),
                    agent_loop_for_owner=lambda owner: self._loop(gateway, owner),
                    authorization_gateway=gateway,
                    require_central_authorization=True,
                )

    def test_production_runtime_accepts_provider_adapter_bound_to_shared_gateway(self):
        class Provider:
            model = "test-model"
            def generate_chat(self, **_kwargs):
                return {"text": "ready", "tool_calls": {"tool_calls": []}}
            def generate_chat_stream(self, **_kwargs):
                yield {"type": "text_delta", "text": "ready"}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            auth = BuiltInAuthService(str(root / "auth.sqlite3"), password_iterations=100_000)
            gateway = AuthorizationGateway(AllowMCPPolicy(), audit=lambda *_: None)

            def owner_loop(owner):
                principal = Principal(owner, "human")
                guarded = GuardedToolRuntime(
                    {}, gateway, principal=principal, policy_revision="1",
                )
                adapter = AuthorizedProviderAgentAdapter(
                    Provider(), authorization_gateway=gateway,
                    principal=principal, policy_revision="1",
                )
                return AgentExecutionLoop(
                    adapter, StructuredToolProtocol(guarded),
                    stream_model=adapter.stream_model,
                )

            runtime = build_conversation_runtime(
                {"storage_backend": "sqlite", "workspace": directory},
                auth_service=auth, agent_loop=self._loop(),
                agent_loop_for_owner=owner_loop,
                authorization_gateway=gateway,
                require_central_authorization=True,
            )
            loop = runtime.application.conversations.agent_loop_for_owner("alice")
            self.assertIs(loop.model.authorization_gateway, gateway)
            self.assertEqual(loop.model.principal.principal_id, "alice")
            runtime.close()


    def test_production_runtime_rejects_provider_principal_context_mismatch(self):
        class Provider:
            model = "test-model"
            def generate_chat(self, **_kwargs):
                return {"text": "ready", "tool_calls": {"tool_calls": []}}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            auth = BuiltInAuthService(str(root / "auth.sqlite3"), password_iterations=100_000)
            gateway = AuthorizationGateway(AllowMCPPolicy(), audit=lambda *_: None)

            def owner_loop(owner):
                guarded_principal = Principal(owner, "human", tenant_id="tenant-a")
                provider_principal = Principal(owner, "human", tenant_id="tenant-b")
                guarded = GuardedToolRuntime(
                    {}, gateway, principal=guarded_principal, policy_revision="1",
                )
                adapter = AuthorizedProviderAgentAdapter(
                    Provider(), authorization_gateway=gateway,
                    principal=provider_principal, policy_revision="1",
                )
                return AgentExecutionLoop(adapter, StructuredToolProtocol(guarded))

            with self.assertRaisesRegex(ValueError, "owner_provider_adapter_principal_mismatch"):
                build_conversation_runtime(
                    {"storage_backend": "sqlite", "workspace": directory},
                    auth_service=auth, agent_loop=self._loop(),
                    agent_loop_for_owner=owner_loop,
                    authorization_gateway=gateway,
                    _security_principal_factory=lambda owner: Principal(owner, 'human', tenant_id='tenant-a'),
                    require_central_authorization=True,
                )

    def test_production_runtime_accepts_mcp_with_central_gateway(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            auth = BuiltInAuthService(str(root / "auth.sqlite3"), password_iterations=100_000)
            oauth = SQLiteOAuthConnectionRegistry(str(root / "oauth.sqlite3"))
            gateway = AuthorizationGateway(AllowMCPPolicy(), audit=lambda *_: None)
            runtime = build_conversation_runtime(
                {"storage_backend": "sqlite", "workspace": directory},
                auth_service=auth, agent_loop=self._loop(),
                agent_loop_for_owner=lambda owner: self._loop(gateway, owner),
                oauth_connections=oauth,
                oauth_disconnect_service_for_provider=lambda _provider: None,
                mcp_runtime=self._mcp_runtime(root, gateway),
                authorization_gateway=gateway,
                require_central_authorization=True,
            )
            self.assertIs(runtime.application.conversations.agent_loop_for_owner("alice").guarded_tool_runtime.authorization_gateway, gateway)
            self.assertIsInstance(runtime.app, AuthenticatedMCPManagementAPI)
            runtime.close()

    def test_production_runtime_rejects_mcp_with_different_gateway(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            auth = BuiltInAuthService(str(root / "auth.sqlite3"), password_iterations=100_000)
            gateway = AuthorizationGateway(AllowMCPPolicy(), audit=lambda *_: None)
            other_gateway = AuthorizationGateway(AllowMCPPolicy(), audit=lambda *_: None)
            with self.assertRaisesRegex(ValueError, "mcp_runtime_must_use_shared_authorization_gateway"):
                build_conversation_runtime(
                    {"storage_backend": "sqlite", "workspace": directory},
                    auth_service=auth, agent_loop=self._loop(),
                    agent_loop_for_owner=lambda owner: self._loop(gateway, owner),
                    authorization_gateway=gateway,
                    mcp_runtime=self._mcp_runtime(root, other_gateway),
                    require_central_authorization=True,
                )

    def test_production_runtime_requires_owner_scoped_loop_factory(self):
        with tempfile.TemporaryDirectory() as directory:
            auth = BuiltInAuthService(str(Path(directory) / "auth.sqlite3"), password_iterations=100_000)
            gateway = AuthorizationGateway(AllowMCPPolicy(), audit=lambda *_: None)
            with self.assertRaisesRegex(ValueError, "owner_scoped_authorized_agent_loops"):
                build_conversation_runtime(
                    {"storage_backend": "sqlite", "workspace": directory},
                    auth_service=auth, agent_loop=self._loop(),
                    authorization_gateway=gateway,
                    require_central_authorization=True,
                )

    def test_production_runtime_rejects_owner_loop_with_different_gateway(self):
        with tempfile.TemporaryDirectory() as directory:
            auth = BuiltInAuthService(str(Path(directory) / "auth.sqlite3"), password_iterations=100_000)
            gateway = AuthorizationGateway(AllowMCPPolicy(), audit=lambda *_: None)
            other_gateway = AuthorizationGateway(AllowMCPPolicy(), audit=lambda *_: None)
            runtime = build_conversation_runtime(
                {"storage_backend": "sqlite", "workspace": directory},
                auth_service=auth, agent_loop=self._loop(),
                agent_loop_for_owner=lambda owner: self._loop(other_gateway, owner),
                authorization_gateway=gateway,
                require_central_authorization=True,
            )
            with self.assertRaisesRegex(ValueError, "not_bound_to_shared_authorization_gateway"):
                runtime.application.conversations.agent_loop_for_owner("alice")
            runtime.close()

    def test_production_runtime_rejects_owner_principal_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            auth = BuiltInAuthService(str(Path(directory) / "auth.sqlite3"), password_iterations=100_000)
            gateway = AuthorizationGateway(AllowMCPPolicy(), audit=lambda *_: None)
            runtime = build_conversation_runtime(
                {"storage_backend": "sqlite", "workspace": directory},
                auth_service=auth, agent_loop=self._loop(),
                agent_loop_for_owner=lambda _owner: self._loop(gateway, "different-owner"),
                authorization_gateway=gateway,
                require_central_authorization=True,
            )
            with self.assertRaisesRegex(ValueError, "owner_agent_loop_principal_mismatch"):
                runtime.application.conversations.agent_loop_for_owner("alice")
            runtime.close()


if __name__ == "__main__":
    unittest.main()
