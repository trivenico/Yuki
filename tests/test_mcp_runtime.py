"""Integration tests for the host-composed MCP application runtime."""
import tempfile
import unittest
from pathlib import Path

from core.approval_audit import SQLiteApprovalAuditLog
from core.mcp_runtime import MCPProviderConnectionConfig, MCPRuntimeService
from core.mcp_tool_policy import MCPToolPolicy, MCPToolRisk
from core.mcp_tool_policy_store import SQLiteMCPToolPolicyStore
from core.oauth_mcp_connection import OAuthMCPConnectionManager
from core.authorization import AuthorizationDecision, Principal
from core.authorization_gateway import AuthorizationGateway


class Vault:
    def get(self, **_kwargs):
        return None


class Refresh:
    def refresh_if_needed(self, **_kwargs):
        return {}


class CapturingClient:
    def __init__(self, *args, **kwargs):
        self.args, self.kwargs = args, kwargs


class MCPPolicy:
    def __init__(self, allowed=True):
        self.allowed = allowed
        self.requests = []

    def authorize(self, request):
        self.requests.append(request)
        return AuthorizationDecision(
            self.allowed, "mcp-decision-1", "1",
            "allowed" if self.allowed else "policy_denied",
        )


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

    def test_non_string_allowlist_host_is_rejected_as_validation_error(self):
        with self.assertRaisesRegex(ValueError, "mcp_allowed_hosts_invalid"):
            MCPProviderConnectionConfig(
                endpoint="https://mcp.example/mcp", allowed_hosts=("mcp.example", None),
            )

    def test_malformed_granted_scopes_are_rejected_cleanly(self):
        for scopes in ((None,), (["nested"],), ("x" * 129,), ("",)):
            with self.subTest(scopes=scopes):
                with self.assertRaisesRegex(ValueError, "granted_oauth_scopes_invalid"):
                    self.service.create_client(
                        owner_id="owner", provider_id="provider", account_id="account",
                        granted_scopes=scopes,
                        owner_approval=lambda _request: True,
                    )

    def test_mcp_execution_is_checked_by_central_gateway_after_owner_approval(self):
        self.policy_store.set_policy(
            owner_id="owner", provider_id="provider", account_id="account",
            policy=self._policy(), expected_revision=1,
        )
        policy = MCPPolicy()
        audit_events = []
        gateway = AuthorizationGateway(
            policy, audit=lambda request, decision: audit_events.append(
                (request.action, request.resource, decision.allowed)
            ),
        )
        service = MCPRuntimeService(
            connection_managers={"provider": self.manager},
            provider_configs={"provider": MCPProviderConnectionConfig(
                endpoint="https://mcp.example/mcp",
                allowed_hosts=("mcp.example",),
                required_scopes=("profile:read",),
            )},
            policy_store=self.policy_store, audit_log=self.audit_log,
            authorization_gateway=gateway,
            principal_for_owner=lambda owner: Principal(owner, "human"),
        )
        client = service.create_client(
            owner_id="owner", provider_id="provider", account_id="account",
            granted_scopes=("profile:read",), owner_approval=lambda _request: True,
        )
        central_authorize = client.kwargs["central_authorize"]
        self.assertIs(central_authorize("read_profile", {"id": "1"}), True)
        self.assertEqual(audit_events, [
            ("mcp:tool:invoke", "mcp:provider:account:read_profile", True),
        ])
        request = policy.requests[0]
        self.assertEqual(request.principal.principal_id, "owner")
        self.assertEqual(request.risk, "low")
        self.assertEqual(request.expected_policy_revision, "1")

    def test_mcp_central_gateway_denial_is_fail_closed(self):
        self.policy_store.set_policy(
            owner_id="owner", provider_id="provider", account_id="account",
            policy=self._policy(), expected_revision=1,
        )
        gateway = AuthorizationGateway(
            MCPPolicy(False), audit=lambda *_: None,
        )
        service = MCPRuntimeService(
            connection_managers={"provider": self.manager},
            provider_configs={"provider": MCPProviderConnectionConfig(
                endpoint="https://mcp.example/mcp",
                allowed_hosts=("mcp.example",),
                required_scopes=("profile:read",),
            )},
            policy_store=self.policy_store, audit_log=self.audit_log,
            authorization_gateway=gateway,
            principal_for_owner=lambda owner: Principal(owner, "human"),
        )
        client = service.create_client(
            owner_id="owner", provider_id="provider", account_id="account",
            granted_scopes=("profile:read",), owner_approval=lambda _request: True,
        )
        self.assertIs(client.kwargs["central_authorize"]("read_profile", {}), False)
        self.assertIs(client.kwargs["central_authorize"]("unknown", {}), False)

    def test_mcp_gateway_requires_matching_trusted_principal(self):
        service = MCPRuntimeService(
            connection_managers={"provider": self.manager},
            provider_configs={"provider": MCPProviderConnectionConfig(
                endpoint="https://mcp.example/mcp",
                allowed_hosts=("mcp.example",),
                required_scopes=("profile:read",),
            )},
            policy_store=self.policy_store, audit_log=self.audit_log,
            authorization_gateway=AuthorizationGateway(
                MCPPolicy(), audit=lambda *_: None,
            ),
            principal_for_owner=lambda _owner: Principal("attacker", "human"),
        )
        with self.assertRaisesRegex(ValueError, "mcp_authenticated_principal_mismatch"):
            service.create_client(
                owner_id="owner", provider_id="provider", account_id="account",
                granted_scopes=("profile:read",), owner_approval=lambda _request: True,
            )


if __name__ == "__main__":
    unittest.main()
