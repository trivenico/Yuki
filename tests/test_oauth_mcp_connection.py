import unittest
from types import SimpleNamespace

from core.oauth_mcp_connection import OAuthMCPConnectionError, OAuthMCPConnectionManager
from core.mcp_tool_policy import MCPToolPolicy, MCPToolPolicyRegistry, MCPToolRisk


class FakeRefresh:
    def __init__(self, status=None, error=None):
        self.status = status or {
            "provider_id": "provider", "account_id": "account",
            "scopes": ("read",), "refreshed": False,
        }
        self.error = error
        self.calls = []

    def refresh_if_needed(self, *, owner_id, account_id):
        self.calls.append((owner_id, account_id))
        if self.error:
            raise self.error
        return self.status


class FakeVault:
    def __init__(self, credential=None):
        self.credential = credential
        self.calls = []

    def get(self, *, owner_id, provider_id, account_id):
        self.calls.append((owner_id, provider_id, account_id))
        return self.credential


class CapturingClient:
    def __init__(self, endpoint, **kwargs):
        self.endpoint = endpoint
        self.kwargs = kwargs


class DiscoveryClient(CapturingClient):
    def initialize(self):
        return {"server_info": {"name": "provider"}, "protocol_version": "2025-03-26"}

    def list_tools(self):
        return [{"name": "read_profile", "description": "Read profile", "input_schema": {}}]


class OAuthMCPConnectionTests(unittest.TestCase):
    def setUp(self):
        self.credential = SimpleNamespace(
            owner_id="owner", provider_id="provider", account_id="account",
            access_token="secret-access-token", scopes=("read",),
        )
        self.vault = FakeVault(self.credential)
        self.refresh = FakeRefresh()
        self.manager = OAuthMCPConnectionManager(
            provider_id="provider", credential_vault=self.vault,
            refresh_service=self.refresh, client_factory=CapturingClient,
        )

    def client(self, **overrides):
        args = {
            "owner_id": "owner", "account_id": "account",
            "endpoint": "https://mcp.example/rpc",
            "allowed_hosts": {"mcp.example"}, "allowed_tools": {"read_document"},
            "approver": lambda _name, _arguments: True,
            "required_scopes": ("read",),
        }
        args.update(overrides)
        return self.manager.create_client(**args)

    def test_policy_client_derives_allowlist_and_uses_risk_approver(self):
        policy = MCPToolPolicy(
            "owner", "provider", "account", "read_profile",
            MCPToolRisk.READ_ONLY, ("read",), 1,
        )
        registry = MCPToolPolicyRegistry(
            owner_id="owner", policies=(policy,), revision=4,
        )
        audit = []
        manager = OAuthMCPConnectionManager(
            provider_id="provider", credential_vault=self.vault,
            refresh_service=self.refresh, client_factory=CapturingClient,
        )
        client = manager.create_policy_client(
            owner_id="owner", account_id="account",
            endpoint="https://mcp.example/rpc", allowed_hosts={"mcp.example"},
            policy_registry=registry, granted_scopes=("read",),
            owner_approval=lambda _request: True, audit=audit.append,
            current_revision=lambda: registry.revision,
            execution_audit=lambda _event, _invocation: None,
        )
        self.assertEqual(client.kwargs["allowed_tools"], ("read_profile",))
        self.assertTrue(client.kwargs["approver"]("read_profile", {"id": "1"}))
        self.assertFalse(client.kwargs["approver"]("unknown_tool", {}))
        self.assertEqual([event["event"] for event in audit],
                         ["requested", "authorized_read_only"])

    def test_policy_client_rejects_untrusted_registry_type(self):
        with self.assertRaisesRegex(OAuthMCPConnectionError, "trusted_tool_policy_registry_required"):
            self.manager.create_policy_client(
                owner_id="owner", account_id="account",
                endpoint="https://mcp.example/rpc", allowed_hosts={"mcp.example"},
                policy_registry=object(), granted_scopes=("read",),
                owner_approval=lambda _request: True, audit=lambda _event: None,
                current_revision=lambda: 1,
                execution_audit=lambda _event, _invocation: None,
            )

    def test_policy_client_rejects_invalid_granted_scopes_cleanly(self):
        policy = MCPToolPolicy(
            "owner", "provider", "account", "read_profile",
            MCPToolRisk.READ_ONLY, ("read",), 1,
        )
        registry = MCPToolPolicyRegistry(
            owner_id="owner", policies=(policy,), revision=4,
        )
        with self.assertRaisesRegex(OAuthMCPConnectionError, "trusted_tool_policy_invalid"):
            self.manager.create_policy_client(
                owner_id="owner", account_id="account",
                endpoint="https://mcp.example/rpc", allowed_hosts={"mcp.example"},
                policy_registry=registry, granted_scopes=None,
                owner_approval=lambda _request: True, audit=lambda _event: None,
                current_revision=lambda: 4,
                execution_audit=lambda _event, _invocation: None,
            )

    def test_token_is_resolved_lazily_and_scoped_to_authenticated_owner(self):
        client = self.client()
        self.assertEqual(self.refresh.calls, [])
        provider = client.kwargs["access_token_provider"]
        self.assertEqual(provider(), "secret-access-token")
        self.assertEqual(self.refresh.calls, [("owner", "account")])
        self.assertEqual(self.vault.calls, [("owner", "provider", "account")])

    def test_required_scopes_are_enforced_before_token_is_returned(self):
        client = self.client(required_scopes=("write",))
        with self.assertRaisesRegex(OAuthMCPConnectionError, "required_oauth_scope_missing"):
            client.kwargs["access_token_provider"]()
        self.assertEqual(self.vault.calls, [])

    def test_provider_and_account_context_mismatch_fails_closed(self):
        self.refresh.status = {
            "provider_id": "other", "account_id": "account", "scopes": ("read",),
        }
        client = self.client()
        with self.assertRaisesRegex(OAuthMCPConnectionError, "credential_context_mismatch"):
            client.kwargs["access_token_provider"]()
        self.assertEqual(self.vault.calls, [])

    def test_missing_credential_fails_closed(self):
        self.vault.credential = None
        client = self.client()
        with self.assertRaisesRegex(OAuthMCPConnectionError, "credential_not_available"):
            client.kwargs["access_token_provider"]()

    def test_refresh_failure_is_not_retried_or_leaked(self):
        self.refresh.error = RuntimeError("sensitive transport details")
        client = self.client()
        with self.assertRaisesRegex(OAuthMCPConnectionError, "credential_not_available") as raised:
            client.kwargs["access_token_provider"]()
        self.assertNotIn("sensitive", str(raised.exception))
        self.assertEqual(len(self.refresh.calls), 1)

    def test_requires_explicit_tool_approver_and_allowlist(self):
        with self.assertRaisesRegex(OAuthMCPConnectionError, "explicit_tool_approver_required"):
            self.client(approver=None)
        with self.assertRaisesRegex(OAuthMCPConnectionError, "explicit_tool_allowlist_required"):
            self.client(allowed_tools=None)

    def test_manager_discovery_binds_provider_and_uses_empty_execution_allowlist(self):
        manager = OAuthMCPConnectionManager(
            provider_id="provider", credential_vault=self.vault,
            refresh_service=self.refresh, client_factory=DiscoveryClient,
        )
        snapshot = manager.discover_tools(
            owner_id="owner", account_id="account",
            endpoint="https://mcp.example/rpc", allowed_hosts={"mcp.example"},
            required_scopes=("read",),
        )
        self.assertEqual(
            (snapshot.owner_id, snapshot.provider_id, snapshot.account_id),
            ("owner", "provider", "account"),
        )
        self.assertFalse(snapshot.tools[0].execution_enabled)
        self.assertFalse(snapshot.tools[0].read_only)

    def test_empty_allowlist_creates_discovery_only_deny_all_client(self):
        client = self.client(allowed_tools=())
        self.assertEqual(client.kwargs["allowed_tools"], ())
        self.assertIsNotNone(client.kwargs["access_token_provider"])
        # The manager does not grant any tool execution permission.
    def test_invalid_owner_is_rejected_before_client_construction(self):
        with self.assertRaisesRegex(OAuthMCPConnectionError, "authenticated_owner_invalid"):
            self.client(owner_id="../other")


if __name__ == "__main__":
    unittest.main()
