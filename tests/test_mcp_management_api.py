import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from api.mcp_management import AuthenticatedMCPManagementAPI
from core.approval_audit import SQLiteApprovalAuditLog
from core.mcp_runtime import MCPProviderConnectionConfig, MCPRuntimeService, MCPTrustedToolDefinition
from core.mcp_tool_policy import MCPToolPolicy, MCPToolRisk
from core.mcp_tool_policy_store import SQLiteMCPToolPolicyStore
from core.oauth_connection_registry import SQLiteOAuthConnectionRegistry
from core.oauth_mcp_connection import OAuthMCPConnectionManager


TOKEN = "valid-session-token-1234567890"


class FakeAuth:
    def resolve_session(self, token):
        return SimpleNamespace(user_id="owner") if token == TOKEN else None


class FakeVault:
    def get(self, **_kwargs):
        return None


class FakeRefresh:
    def refresh_if_needed(self, **_kwargs):
        return {}


class DiscoveryClient:
    def __init__(self, *_args, **_kwargs):
        pass

    def initialize(self):
        return {
            "protocol_version": "2025-03-26",
            "server_info": {"name": "example-mcp", "version": "1.0"},
        }

    def list_tools(self):
        return [{
            "name": "read_profile",
            "description": "Read the current profile",
            "input_schema": {"type": "object", "properties": {"id": {"type": "string"}}},
        }, {
            "name": "publish",
            "description": "Publish content",
            "input_schema": {"type": "object"},
        }]


class MCPManagementAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.oauth = SQLiteOAuthConnectionRegistry(str(root / "oauth.sqlite3"))
        self.oauth.register(
            owner_id="owner", provider_id="provider", account_id="account",
            scopes=("profile:read",),
        )
        self.policies = SQLiteMCPToolPolicyStore(str(root / "policies.sqlite3"))
        self.audit = SQLiteApprovalAuditLog(str(root / "audit.sqlite3"))
        manager = OAuthMCPConnectionManager(
            provider_id="provider", credential_vault=FakeVault(),
            refresh_service=FakeRefresh(), client_factory=DiscoveryClient,
        )
        self.runtime = MCPRuntimeService(
            connection_managers={"provider": manager},
            provider_configs={"provider": MCPProviderConnectionConfig(
                endpoint="https://mcp.example/mcp",
                allowed_hosts=("mcp.example",),
                required_scopes=("profile:read",),
            )},
            policy_store=self.policies, audit_log=self.audit,
            trusted_tool_definitions={
                ("provider", "read_profile"): MCPTrustedToolDefinition(
                    tool_name="read_profile", risk=MCPToolRisk.READ_ONLY,
                    required_scopes=("profile:read",),
                ),
            },
        )
        self.app = AuthenticatedMCPManagementAPI(
            lambda *_args: None, FakeAuth(), self.oauth, self.runtime,
        )

    def request(self, method, path, token=TOKEN, extra_headers=(), body=None):
        sent = []
        async def receive():
            return {"type": "http.request", "body": body or b"", "more_body": False}
        async def send(message):
            sent.append(message)
        headers = list(extra_headers)
        if token is not None:
            headers.append((b"authorization", ("Bearer " + token).encode()))
        scope = {"type": "http", "method": method, "path": path, "headers": headers}
        asyncio.run(self.app(scope, receive, send))
        return sent[0]["status"], json.loads(sent[1]["body"])

    def test_discovery_is_authenticated_and_explicitly_inert(self):
        status, body = self.request(
            "GET", "/v1/integrations/mcp/provider/account/tools",
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["tools_trust"], "untrusted_remote_server")
        self.assertFalse(body["execution_enabled"])
        self.assertEqual([item["name"] for item in body["tools"]], ["read_profile", "publish"])
        self.assertTrue(all(item["execution_enabled"] is False for item in body["tools"]))
        self.assertTrue(all(item["read_only"] is False for item in body["tools"]))
        self.assertNotIn("endpoint", body)
        self.assertNotIn("access_token", json.dumps(body))

    def test_missing_or_ambiguous_bearer_header_is_rejected(self):
        status, _ = self.request(
            "GET", "/v1/integrations/mcp/provider/account/tools", token=None,
        )
        self.assertEqual(status, 401)
        status, _ = self.request(
            "GET", "/v1/integrations/mcp/provider/account/tools",
            extra_headers=((b"authorization", ("Bearer " + TOKEN).encode()), (b"authorization", ("Bearer " + TOKEN).encode())),
        )
        self.assertEqual(status, 401)

    def test_account_must_belong_to_authenticated_owner(self):
        status, body = self.request(
            "GET", "/v1/integrations/mcp/provider/other/tools",
        )
        self.assertEqual(status, 404)
        self.assertEqual(body["error"], "connection_not_found")

    def test_policy_listing_is_owner_scoped_and_does_not_enable_execution(self):
        self.policies.set_policy(
            owner_id="owner", provider_id="provider", account_id="account",
            policy=MCPToolPolicy(
                owner_id="owner", provider_id="provider", account_id="account",
                tool_name="read_profile", risk=MCPToolRisk.READ_ONLY,
                required_scopes=("profile:read",), version=1, enabled=True,
            ),
            expected_revision=1,
        )
        status, body = self.request(
            "GET", "/v1/integrations/mcp/provider/account/policies",
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["revision"], 2)
        self.assertEqual(body["policies"], [{
            "tool_name": "read_profile", "risk": "read_only",
            "required_scopes": ["profile:read"], "version": 1, "enabled": True,
        }])


    def test_owner_confirmed_policy_enable_uses_host_trusted_risk(self):
        payload = json.dumps({
            "enabled": True, "expected_revision": 1, "confirm": True,
        }).encode()
        status, body = self.request(
            "PUT", "/v1/integrations/mcp/provider/account/policies/read_profile",
            body=payload,
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["revision"], 2)
        self.assertEqual(body["policies"][0]["risk"], "read_only")
        self.assertTrue(body["policies"][0]["enabled"])
        self.assertTrue(self.audit.verify_chain())

    def test_client_cannot_override_host_trusted_risk(self):
        payload = json.dumps({
            "enabled": True, "expected_revision": 1, "confirm": True,
            "risk": "financial",
        }).encode()
        status, body = self.request(
            "PUT", "/v1/integrations/mcp/provider/account/policies/read_profile",
            body=payload,
        )
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "invalid_request")
        self.assertEqual(self.policies.load_registry(
            owner_id="owner", provider_id="provider", account_id="account",
        ).policies, ())

    def test_stale_policy_revision_is_rejected(self):
        payload = json.dumps({
            "enabled": True, "expected_revision": 9, "confirm": True,
        }).encode()
        status, body = self.request(
            "PUT", "/v1/integrations/mcp/provider/account/policies/read_profile",
            body=payload,
        )
        self.assertEqual(status, 409)
        self.assertEqual(body["error"], "stale_policy_revision")

    def test_audit_failure_prevents_policy_mutation(self):
        class BrokenAudit:
            def append(self, **_kwargs):
                raise OSError("audit unavailable")
        self.runtime._audit = BrokenAudit()
        payload = json.dumps({
            "enabled": True, "expected_revision": 1, "confirm": True,
        }).encode()
        status, _ = self.request(
            "PUT", "/v1/integrations/mcp/provider/account/policies/read_profile",
            body=payload,
        )
        self.assertEqual(status, 502)
        self.assertEqual(self.policies.load_registry(
            owner_id="owner", provider_id="provider", account_id="account",
        ).policies, ())

    def test_policy_disable_advances_revision_and_revokes_policy(self):
        self.policies.set_policy(
            owner_id="owner", provider_id="provider", account_id="account",
            policy=MCPToolPolicy(
                owner_id="owner", provider_id="provider", account_id="account",
                tool_name="read_profile", risk=MCPToolRisk.READ_ONLY,
                required_scopes=("profile:read",), version=1, enabled=True,
            ), expected_revision=1,
        )
        payload = json.dumps({
            "enabled": False, "expected_revision": 2, "confirm": True,
        }).encode()
        status, body = self.request(
            "PUT", "/v1/integrations/mcp/provider/account/policies/read_profile",
            body=payload,
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["revision"], 3)
        self.assertFalse(body["policies"][0]["enabled"])

    def test_registered_connection_without_required_scope_is_denied(self):
        self.oauth.remove(owner_id="owner", provider_id="provider", account_id="account")
        self.oauth.register(
            owner_id="owner", provider_id="provider", account_id="account",
            scopes=("profile:basic",),
        )
        status, body = self.request(
            "GET", "/v1/integrations/mcp/provider/account/tools",
        )
        self.assertEqual(status, 403)
        self.assertEqual(body["error"], "required_oauth_scope_missing")

    def test_unknown_provider_fails_closed(self):
        status, body = self.request(
            "GET", "/v1/integrations/mcp/unknown/account/tools",
        )
        self.assertEqual(status, 404)
        self.assertEqual(body["error"], "provider_not_configured")

    def test_non_get_method_is_rejected(self):
        status, body = self.request(
            "POST", "/v1/integrations/mcp/provider/account/tools",
        )
        self.assertEqual(status, 405)
        self.assertEqual(body["error"], "method_not_allowed")


if __name__ == "__main__":
    unittest.main()
