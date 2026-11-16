import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from api.mcp_management import AuthenticatedMCPManagementAPI
from api.runtime import build_conversation_runtime
from core.agent_execution_loop import AgentExecutionLoop
from core.approval_audit import SQLiteApprovalAuditLog
from core.auth import BuiltInAuthService
from core.guarded_tool_runtime import GuardedToolRuntime
from core.mcp_runtime import MCPProviderConnectionConfig, MCPRuntimeService
from core.network_firewall import NetworkFirewall
from core.mcp_tool_policy_store import SQLiteMCPToolPolicyStore
from core.oauth_connection_registry import SQLiteOAuthConnectionRegistry
from core.oauth_mcp_connection import OAuthMCPConnectionManager
from core.structured_tool_protocol import StructuredToolProtocol


class NoApprovals:
    def authorize_tool(self, _name):
        return False


class FakeVault:
    def get(self, **_kwargs):
        return None


class FakeRefresh:
    def refresh_if_needed(self, **_kwargs):
        return {}


class MCPRuntimeWiringTests(unittest.TestCase):
    def _loop(self):
        def model(**_kwargs):
            return {"text": "ready", "tool_calls": {"tool_calls": []}}
        protocol = StructuredToolProtocol(GuardedToolRuntime({}, NoApprovals()))
        return AgentExecutionLoop(model, protocol)

    def _mcp_runtime(self, root, *, firewall_policy=None, refresh_firewall_policy=None):
        refresh = FakeRefresh()
        if refresh_firewall_policy is not None:
            refresh.firewall_policy = refresh_firewall_policy
        manager = OAuthMCPConnectionManager(
            provider_id="provider", credential_vault=FakeVault(),
            refresh_service=refresh, client_factory=lambda *_args, **_kwargs: None,
            firewall_policy=firewall_policy,
        )
        return MCPRuntimeService(
            connection_managers={"provider": manager},
            provider_configs={"provider": MCPProviderConnectionConfig(
                endpoint="https://mcp.example/mcp", allowed_hosts=("mcp.example",),
                required_scopes=("profile:read",),
            )},
            policy_store=SQLiteMCPToolPolicyStore(str(root / "policies.sqlite3")),
            audit_log=SQLiteApprovalAuditLog(str(root / "audit.sqlite3")),
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

    def test_runtime_accepts_mcp_stack_with_exact_shared_firewall(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            auth = BuiltInAuthService(str(root / "auth.sqlite3"), password_iterations=100_000)
            oauth = SQLiteOAuthConnectionRegistry(str(root / "oauth.sqlite3"))
            firewall = NetworkFirewall(allowed_hosts=("mcp.example",))
            runtime = build_conversation_runtime(
                {"storage_backend": "sqlite", "workspace": directory},
                auth_service=auth, agent_loop=self._loop(),
                oauth_connections=oauth,
                oauth_disconnect_service_for_provider=lambda _provider: None,
                mcp_runtime=self._mcp_runtime(
                    root, firewall_policy=firewall, refresh_firewall_policy=firewall,
                ),
                network_firewall=firewall,
            )
            self.assertIsInstance(runtime.app, AuthenticatedMCPManagementAPI)
            runtime.close()

    def test_runtime_rejects_mcp_stack_with_mismatched_refresh_firewall(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            auth = BuiltInAuthService(str(root / "auth.sqlite3"), password_iterations=100_000)
            oauth = SQLiteOAuthConnectionRegistry(str(root / "oauth.sqlite3"))
            firewall = NetworkFirewall(allowed_hosts=("mcp.example",))
            other_firewall = NetworkFirewall(allowed_hosts=("mcp.example",))
            with self.assertRaisesRegex(ValueError, "exact API network firewall policy"):
                build_conversation_runtime(
                    {"storage_backend": "sqlite", "workspace": directory},
                    auth_service=auth, agent_loop=self._loop(),
                    oauth_connections=oauth,
                    oauth_disconnect_service_for_provider=lambda _provider: None,
                    mcp_runtime=self._mcp_runtime(
                        root, firewall_policy=firewall,
                        refresh_firewall_policy=other_firewall,
                    ),
                    network_firewall=firewall,
                )

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


if __name__ == "__main__":
    unittest.main()
