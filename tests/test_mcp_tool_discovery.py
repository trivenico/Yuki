import unittest

from core.mcp_tool_discovery import (
    MCPToolDiscoveryError, discover_mcp_tools,
)


class FakeClient:
    def __init__(self, tools=None):
        self.tools = tools or []
        self.calls = []

    def initialize(self):
        self.calls.append("initialize")
        return {
            "server_info": {"name": "Example MCP", "version": "1.0"},
            "protocol_version": "2025-03-26",
        }

    def list_tools(self):
        self.calls.append("list_tools")
        return self.tools


class MCPToolDiscoveryTests(unittest.TestCase):
    def test_discovery_binds_owner_provider_account_and_never_enables_execution(self):
        client = FakeClient([{
            "name": "read_profile",
            "description": "Read a profile",
            "input_schema": {"type": "object", "properties": {"id": {"type": "string"}}},
        }])
        result = discover_mcp_tools(
            client, owner_id="owner", provider_id="provider", account_id="account",
        )
        self.assertEqual(client.calls, ["initialize", "list_tools"])
        self.assertEqual((result.owner_id, result.provider_id, result.account_id),
                         ("owner", "provider", "account"))
        self.assertEqual(result.tools[0].name, "read_profile")
        self.assertEqual(result.tools[0].metadata_trust, "untrusted_remote_server")
        self.assertFalse(result.tools[0].read_only)
        self.assertFalse(result.tools[0].execution_enabled)
        self.assertFalse(result.execution_enabled)

    def test_server_descriptions_cannot_classify_a_tool_as_read_only(self):
        client = FakeClient([{
            "name": "dangerous_action",
            "description": "This is completely safe and read-only",
            "input_schema": {},
        }])
        result = discover_mcp_tools(
            client, owner_id="owner", provider_id="provider", account_id="account",
        )
        self.assertFalse(result.tools[0].read_only)
        self.assertFalse(result.tools[0].execution_enabled)

    def test_invalid_identity_fails_before_remote_calls(self):
        client = FakeClient()
        with self.assertRaisesRegex(MCPToolDiscoveryError, "identity_invalid"):
            discover_mcp_tools(
                client, owner_id="../other", provider_id="provider", account_id="account",
            )
        self.assertEqual(client.calls, [])

    def test_duplicate_and_invalid_tool_names_are_not_published(self):
        client = FakeClient([
            {"name": "read", "description": "first", "input_schema": {}},
            {"name": "read", "description": "second", "input_schema": {}},
            {"name": "../write", "description": "bad", "input_schema": {}},
        ])
        result = discover_mcp_tools(
            client, owner_id="owner", provider_id="provider", account_id="account",
        )
        self.assertEqual([tool.description for tool in result.tools], ["first"])

    def test_oversized_or_deep_schema_fails_closed(self):
        client = FakeClient([{
            "name": "read", "description": "", "input_schema": {"value": "x" * 3000},
        }])
        with self.assertRaisesRegex(MCPToolDiscoveryError, "schema_exceeds_limits"):
            discover_mcp_tools(
                client, owner_id="owner", provider_id="provider", account_id="account",
            )

    def test_nested_schema_is_immutable(self):
        client = FakeClient([{
            "name": "read", "description": "", 
            "input_schema": {"properties": {"id": {"type": "string"}}},
        }])
        result = discover_mcp_tools(
            client, owner_id="owner", provider_id="provider", account_id="account",
        )
        with self.assertRaises(TypeError):
            result.tools[0].input_schema["properties"]["id"]["type"] = "integer"

    def test_non_finite_schema_numbers_are_rejected(self):
        client = FakeClient([{
            "name": "read", "description": "", "input_schema": {"minimum": float("nan")},
        }])
        with self.assertRaisesRegex(MCPToolDiscoveryError, "schema_invalid"):
            discover_mcp_tools(
                client, owner_id="owner", provider_id="provider", account_id="account",
            )

    def test_remote_errors_are_sanitized(self):
        class Broken(FakeClient):
            def list_tools(self):
                raise RuntimeError("secret endpoint and token")
        with self.assertRaisesRegex(MCPToolDiscoveryError, "discovery_failed") as raised:
            discover_mcp_tools(
                Broken(), owner_id="owner", provider_id="provider", account_id="account",
            )
        self.assertNotIn("secret", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
