"""Integration tests for the host-composed MCP application runtime."""
import tempfile
import unittest
from pathlib import Path

from core.approval_audit import SQLiteApprovalAuditLog
from core.mcp_runtime import MCPProviderConnectionConfig, MCPRuntimeService
from core.mcp_tool_policy import MCPToolPolicy, MCPToolRisk
from core.mcp_tool_policy_store import SQLiteMCPToolPolicyStore
from core.oauth_mcp_connection import OAuthMCPConnectionManager


class Vault:
    def get(self, **_kwargs):
        return None


class Refresh:
    def refresh_if_needed(self, **_kwargs):
        return {}


class CapturingClient:
    def __init__(self, *args, **kwargs):
        self.args, self.kwargs = args, kwargs


class MCPRuntimeServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.policy_store = SQLiteMCPToolPolicyStore(
            str(Path(self.tmp.name) / "policies.sqlite3")
        )
        self.audit_log = SQLiteApprovalAuditLog(
            str(Path(self.tmp.name) / "audit.sqlite3")
        )
        self.manager = OAuthMCPConnectionManager(
            provider_id="provider", credential_vault=Vault(),
            refresh_service=Refresh(), client_factory=CapturingClient,
        )
        self.service = MCPRuntimeService(
            connection_managers={"provider": self.manager},
            provider_configs={"provider": MCPProviderConnectionConfig(
                endpoint="https://mcp.example/mcp",
                allowed_hosts=("mcp.example",),
                required_scopes=("profile:read",),
            )},
            policy_store=self.policy_store, audit_log=self.audit_log,
        )

    def _policy(self, *, enabled=True):
        return MCPToolPolicy(
            owner_id="owner", provider_id="provider", account_id="account",
            tool_name="read_profile", risk=MCPToolRisk.READ_ONLY,
            required_scopes=("profile:read",), version=1, enabled=enabled,
        )

    def test_builds_client_from_owner_scoped_durable_policy(self):
        self.policy_store.set_policy(
            owner_id="owner", provider_id="provider", account_id="account",
            policy=self._policy(), expected_revision=1,
        )
        client = self.service.create_client(
            owner_id="owner", provider_id="provider", account_id="account",
            granted_scopes=("profile:read",),
            owner_approval=lambda _request: True,
        )
        self.assertEqual(client.kwargs["allowed_tools"], ("read_profile",))
        self.assertTrue(client.kwargs["approver"]("read_profile", {"id": "1"}))
        self.assertFalse(client.kwargs["approver"]("unlisted", {}))
        self.assertTrue(self.audit_log.verify_chain())

    def test_policy_change_fences_existing_client(self):
        self.policy_store.set_policy(
            owner_id="owner", provider_id="provider", account_id="account",
            policy=self._policy(), expected_revision=1,
        )
        client = self.service.create_client(
            owner_id="owner", provider_id="provider", account_id="account",
            granted_scopes=("profile:read",),
            owner_approval=lambda _request: True,
        )
        self.policy_store.remove_policy(
            owner_id="owner", provider_id="provider", account_id="account",
            tool_name="read_profile", expected_revision=2,
        )
        self.assertFalse(client.kwargs["approver"]("read_profile", {"id": "1"}))

    def test_missing_required_scope_fails_before_client_creation(self):
        with self.assertRaisesRegex(ValueError, "required_oauth_scope_missing"):
            self.service.create_client(
                owner_id="owner", provider_id="provider", account_id="account",
                granted_scopes=(), owner_approval=lambda _request: True,
            )

    def test_unconfigured_provider_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "mcp_provider_not_configured"):
            self.service.create_client(
                owner_id="owner", provider_id="unknown", account_id="account",
                granted_scopes=("profile:read",),
                owner_approval=lambda _request: True,
            )


if __name__ == "__main__":
    unittest.main()
