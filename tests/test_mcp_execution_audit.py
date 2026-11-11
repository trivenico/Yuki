"""Tests for durable MCP execution lifecycle auditing."""
import json
import tempfile
import unittest
from pathlib import Path

from core.approval_audit import MCPExecutionAudit, MCPToolPolicyAudit, SQLiteApprovalAuditLog
from core.mcp_client import MCPError, MCPHTTPClient


class FakeTransport:
    def __init__(self, mode="success"):
        self.mode, self.calls = mode, 0

    def post_json(self, endpoint, *, payload, headers):
        self.calls += 1
        if self.mode == "transport":
            raise RuntimeError("connection dropped")
        if self.mode == "remote_error":
            body = {"jsonrpc": "2.0", "id": payload["id"], "error": {"code": -32000, "message": "denied"}}
        else:
            body = {"jsonrpc": "2.0", "id": payload["id"], "result": {"content": [{"type": "text", "text": "ok"}]}}
        return 200, {"content-type": "application/json"}, json.dumps(body).encode()


class MCPExecutionAuditTests(unittest.TestCase):
    def _client(self, log, transport, approver=True):
        events = MCPExecutionAudit(log, owner_id="owner", provider_id="provider",
                                   account_id="account", tool_name="publish")
        return MCPHTTPClient("https://mcp.example/mcp", allowed_hosts=("mcp.example",),
            allowed_tools=("publish",), approver=lambda _n, _a: approver,
            execution_audit=events, http_transport=transport)

    def test_success_records_start_and_completion_without_payloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "audit.sqlite3")
            log = SQLiteApprovalAuditLog(path)
            transport = FakeTransport()
            self._client(log, transport).call_tool("publish", {"secret_payload": "must-not-be-stored"})
            self.assertEqual([e["event_type"] for e in reversed(log.list_events())],
                             ["execution_started", "executed"])
            self.assertTrue(log.verify_chain())
            with open(path, "rb") as handle:
                raw = handle.read()
            self.assertNotIn(b"must-not-be-stored", raw)
            self.assertNotIn(b"owner", raw)
            self.assertEqual(transport.calls, 1)

    def test_transport_failure_is_unknown_and_never_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = SQLiteApprovalAuditLog(str(Path(tmp) / "audit.sqlite3"))
            transport = FakeTransport("transport")
            with self.assertRaises(Exception):
                self._client(log, transport).call_tool("publish", {})
            self.assertEqual([e["event_type"] for e in reversed(log.list_events())],
                             ["execution_started", "outcome_unknown"])
            self.assertEqual(transport.calls, 1)

    def test_explicit_remote_error_is_failure_not_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = SQLiteApprovalAuditLog(str(Path(tmp) / "audit.sqlite3"))
            transport = FakeTransport("remote_error")
            with self.assertRaises(MCPError):
                self._client(log, transport).call_tool("publish", {})
            self.assertEqual([e["event_type"] for e in reversed(log.list_events())],
                             ["execution_started", "execution_failed"])
            self.assertEqual(transport.calls, 1)

    def test_audit_failure_prevents_dispatch(self):
        transport = FakeTransport()
        def broken(_event, _invocation, _tool_name):
            raise RuntimeError("audit offline")
        client = MCPHTTPClient("https://mcp.example/mcp", allowed_hosts=("mcp.example",),
            allowed_tools=("publish",), approver=lambda _n, _a: True,
            execution_audit=broken, http_transport=transport)
        with self.assertRaises(MCPError):
            client.call_tool("publish", {})
        self.assertEqual(transport.calls, 0)

    def test_policy_decisions_use_shared_hash_chained_audit_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = SQLiteApprovalAuditLog(str(Path(tmp) / "audit.sqlite3"))
            policy_audit = MCPToolPolicyAudit(log)
            for event in ("requested", "owner_approved", "authorized_read_only", "denied"):
                policy_audit({
                    "event": event, "owner_id": "owner", "provider_id": "provider",
                    "account_id": "account", "tool_name": "publish",
                    "arguments": {"secret": "never persist"},
                })
            self.assertEqual([e["event_type"] for e in reversed(log.list_events())],
                             ["requested", "approved", "approved", "denied"])
            self.assertTrue(log.verify_chain())
            with open(log.database_path, "rb") as handle:
                self.assertNotIn(b"never persist", handle.read())

    def test_approval_denial_does_not_create_execution_events(self):
        transport, events = FakeTransport(), []
        client = MCPHTTPClient("https://mcp.example/mcp", allowed_hosts=("mcp.example",),
            allowed_tools=("publish",), approver=lambda _n, _a: False,
            execution_audit=lambda event, invocation, tool_name: events.append((event, invocation, tool_name)),
            http_transport=transport)
        with self.assertRaises(MCPError):
            client.call_tool("publish", {})
        self.assertEqual(events, [])
        self.assertEqual(transport.calls, 0)


if __name__ == "__main__":
    unittest.main()
