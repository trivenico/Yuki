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

    def test_empty_allowlist_allows_discovery_but_denies_every_tool_call(self):
        transport = FakeTransport([])
        client = MCPHTTPClient(
            "https://mcp.example.com/rpc", allowed_hosts={"mcp.example.com"},
            allowed_tools=(), approver=lambda _name, _arguments: True,
            http_transport=transport,
        )
        with self.assertRaisesRegex(MCPError, "not explicitly allow-listed"):
            client.call_tool("read_profile", {})
        self.assertEqual(transport.calls, [])

    def test_rejects_endpoint_not_on_allow_list(self):
        with self.assertRaises(MCPError):
            MCPHTTPClient("https://attacker.example/rpc", allowed_hosts={"mcp.example.com"})


    def test_execution_lifecycle_records_started_then_confirmed_success(self):
        transport = FakeTransport([
            (200, {"content-type": "application/json"},
             b'{"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"ok"}]}}')
        ])
        events = []
        approvals = []
        def approver(name, arguments, execution_id):
            approvals.append((name, arguments, execution_id))
            return True
        client = MCPHTTPClient(
            "https://mcp.example.com/rpc", allowed_hosts={"mcp.example.com"},
            allowed_tools={"write_note"}, approver=approver,
            execution_audit=events.append, http_transport=transport,
        )
        result = client.call_tool("write_note", {"text": "private payload"})
        self.assertTrue(result["untrusted"])
        self.assertEqual([event["event"] for event in events],
                         ["execution_started", "executed"])
        self.assertEqual(events[0]["execution_id"], events[1]["execution_id"])
        self.assertEqual(approvals[0][2], events[0]["execution_id"])
        self.assertNotIn("private payload", str(events))

    def test_transport_failure_after_start_is_outcome_unknown_and_not_retried(self):
        from core.mcp_client import MCPExecutionOutcomeUnknown
        transport = FakeTransport([MCPPinnedTransportError("private transport detail")])
        events = []
        client = MCPHTTPClient(
            "https://mcp.example.com/rpc", allowed_hosts={"mcp.example.com"},
            allowed_tools={"write_note"},
            approver=lambda _name, _arguments, _execution_id: True,
            execution_audit=events.append, http_transport=transport,
        )
        with self.assertRaisesRegex(MCPExecutionOutcomeUnknown, "do not retry"):
            client.call_tool("write_note", {"text": "private"})
        self.assertEqual([event["event"] for event in events],
                         ["execution_started", "outcome_unknown"])
        self.assertEqual(len(transport.calls), 1)

    def test_execution_start_audit_failure_prevents_remote_dispatch(self):
        def broken(_event):
            raise RuntimeError("audit unavailable")
        transport = FakeTransport([])
        client = MCPHTTPClient(
            "https://mcp.example.com/rpc", allowed_hosts={"mcp.example.com"},
            allowed_tools={"write_note"},
            approver=lambda _name, _arguments, _execution_id: True,
            execution_audit=broken, http_transport=transport,
        )
        with self.assertRaisesRegex(MCPError, "was not dispatched"):
            client.call_tool("write_note", {})
        self.assertEqual(transport.calls, [])

    def test_terminal_audit_failure_is_reported_as_unknown_without_retry(self):
        from core.mcp_client import MCPExecutionOutcomeUnknown
        transport = FakeTransport([
            (200, {"content-type": "application/json"},
             b'{"jsonrpc":"2.0","id":1,"result":{}}')
        ])
        def broken(event):
            if event["event"] == "executed":
                raise RuntimeError("audit unavailable")
        client = MCPHTTPClient(
            "https://mcp.example.com/rpc", allowed_hosts={"mcp.example.com"},
            allowed_tools={"write_note"},
            approver=lambda _name, _arguments, _execution_id: True,
            execution_audit=broken, http_transport=transport,
        )
        with self.assertRaisesRegex(MCPExecutionOutcomeUnknown, "do not retry"):
            client.call_tool("write_note", {})
        self.assertEqual(len(transport.calls), 1)

    def test_remote_tool_error_response_is_a_confirmed_failure(self):
        transport = FakeTransport([
            (200, {"content-type": "application/json"},
             b'{"jsonrpc":"2.0","id":1,"result":{"isError":true,"content":[]}}')
        ])
        events = []
        client = MCPHTTPClient(
            "https://mcp.example.com/rpc", allowed_hosts={"mcp.example.com"},
            allowed_tools={"write_note"},
            approver=lambda _name, _arguments, _execution_id: True,
            execution_audit=events.append, http_transport=transport,
        )
        client.call_tool("write_note", {})
        self.assertEqual([event["event"] for event in events],
                         ["execution_started", "execution_failed"])


if __name__ == "__main__":
    unittest.main()
