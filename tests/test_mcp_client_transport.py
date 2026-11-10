import unittest

from core.mcp_client import MCPError, MCPHTTPClient
from core.mcp_pinned_transport import MCPPinnedTransportError


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
    def post_json(self, endpoint, *, payload, headers):
        self.calls.append((endpoint, payload, headers))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class MCPHTTPClientPinnedTransportTests(unittest.TestCase):
    def test_uses_injected_transport_and_propagates_session_id(self):
        transport = FakeTransport([
            (200, {"content-type":"application/json", "mcp-session-id":"session-1"},
             b'{"jsonrpc":"2.0","id":1,"result":{"serverInfo":{"name":"test"}}}'),
            (202, {"mcp-session-id":"session-1"}, b""),
        ])
        client = MCPHTTPClient(
            "https://mcp.example.com/rpc", allowed_hosts={"mcp.example.com"},
            http_transport=transport,
        )
        result = client.initialize()
        self.assertEqual(transport.calls[0][2]["Accept"], "application/json, text/event-stream")
        self.assertEqual(result["server_info"]["name"], "test")
        self.assertEqual(client.session_id, "session-1")
        self.assertEqual(transport.calls[1][2]["Mcp-Session-Id"], "session-1")
        self.assertEqual(transport.calls[1][2]["MCP-Protocol-Version"], client.PROTOCOL_VERSION)

    def test_rejects_mismatched_jsonrpc_id(self):
        transport = FakeTransport([
            (200, {"content-type":"application/json"},
             b'{"jsonrpc":"2.0","id":99,"result":{}}')
        ])
        client = MCPHTTPClient(
            "https://mcp.example.com/rpc", allowed_hosts={"mcp.example.com"},
            http_transport=transport,
        )
        with self.assertRaisesRegex(MCPError, "mismatched"):
            client._rpc("tools/list", {})

    def test_sanitizes_transport_failure(self):
        transport = FakeTransport([MCPPinnedTransportError("private detail")])
        client = MCPHTTPClient(
            "https://mcp.example.com/rpc", allowed_hosts={"mcp.example.com"},
            http_transport=transport,
        )
        with self.assertRaises(MCPError) as caught:
            client._rpc("initialize", {})
        self.assertNotIn("private detail", str(caught.exception))

    def test_rejects_endpoint_not_on_allow_list(self):
        with self.assertRaises(MCPError):
            MCPHTTPClient("https://attacker.example/rpc", allowed_hosts={"mcp.example.com"})


if __name__ == "__main__":
    unittest.main()
