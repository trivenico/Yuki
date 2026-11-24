import tempfile
import unittest
from pathlib import Path

from api.mcp_management import AuthenticatedMCPManagementAPI
from api.runtime import build_conversation_runtime
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


class MCPRuntimeWiringTests(unittest.TestCase):
    def _loop(self):
        def model(**_kwargs):
            return {"text": "ready", "tool_calls": {"tool_calls": []}}
        protocol = StructuredToolProtocol(GuardedToolRuntime({}, NoApprovals()))
        return AgentExecutionLoop(model, protocol)

    def _mcp_runtime(self, root, *, central=False):
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
            authorization_gateway=(
                AuthorizationGateway(AllowMCPPolicy(), audit=lambda *_: None)
                if central else None
            ),
            principal_for_owner=(lambda owner: Principal(owner, "human")) if central else None,
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
            with self.assertRaisesRegex(ValueError, "production_runtime_requires_central_mcp_authorization"):
                build_conversation_runtime(
                    {"storage_backend": "sqlite", "workspace": directory},
                    auth_service=auth, agent_loop=self._loop(),
                    oauth_connections=oauth,
                    oauth_disconnect_service_for_provider=lambda _provider: None,
                    mcp_runtime=self._mcp_runtime(root),
                    require_central_authorization=True,
                )

    def test_production_runtime_accepts_mcp_with_central_gateway(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            auth = BuiltInAuthService(str(root / "auth.sqlite3"), password_iterations=100_000)
            oauth = SQLiteOAuthConnectionRegistry(str(root / "oauth.sqlite3"))
            runtime = build_conversation_runtime(
                {"storage_backend": "sqlite", "workspace": directory},
                auth_service=auth, agent_loop=self._loop(),
                oauth_connections=oauth,
                oauth_disconnect_service_for_provider=lambda _provider: None,
                mcp_runtime=self._mcp_runtime(root, central=True),
                require_central_authorization=True,
            )
            self.assertIsInstance(runtime.app, AuthenticatedMCPManagementAPI)
            runtime.close()


if __name__ == "__main__":
    unittest.main()
