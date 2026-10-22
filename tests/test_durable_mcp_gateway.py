"""Integration tests for durable authorization around MCP tool calls."""
from core.approval_audit import SQLiteApprovalAuditLog
from core.approval_flow import ApprovalCoordinator
from core.durable_mcp_gateway import DurableMCPGateway
from core.execution_ledger import SQLiteExecutionLedger
from core.mcp_client import MCPHTTPClient
from core.pending_approvals import SQLitePendingApprovalStore


class StubMCPClient(MCPHTTPClient):
    def __init__(self):
        # Avoid network construction; the gateway only needs the client's public API.
        self.allowed_tools = frozenset({"read_status", "write_setting"})
        self.approver = lambda name, arguments: True
        self.calls = []

    def call_tool(self, name, arguments):
        if name not in self.allowed_tools:
            raise RuntimeError("not allow-listed")
        if self.approver(name, arguments) is not True:
            raise RuntimeError("not approved")
        self.calls.append((name, arguments))
        return {"ok": True, "name": name}


def make_gateway(tmp_path, *, confirm=lambda action: True):
    coordinator = ApprovalCoordinator(
        pending_store=SQLitePendingApprovalStore(str(tmp_path / "pending.sqlite3")),
        audit_log=SQLiteApprovalAuditLog(str(tmp_path / "audit.sqlite3")),
        execution_ledger=SQLiteExecutionLedger(str(tmp_path / "ledger.sqlite3")),
    )
    client = StubMCPClient()
    gateway = DurableMCPGateway(
        client=client,
        coordinator=coordinator,
        actor="integration-test-owner",
        session_id="mcp-test-session",
        confirm=confirm,
    )
    return gateway, client, coordinator


def test_mcp_gateway_requires_durable_approval_and_audits_execution(tmp_path):
    gateway, client, coordinator = make_gateway(tmp_path)
    outcome = gateway.call_tool("write_setting", {"setting": "safe", "value": True})
    assert outcome.ok is True
    assert outcome.result == {"ok": True, "name": "write_setting"}
    assert client.calls == [("write_setting", {"setting": "safe", "value": True})]
    assert coordinator.audit_log.verify_chain() is True
    events = [item["event_type"] for item in coordinator.audit_log.list_events()]
    assert "requested" in events
    assert "approved" in events
    assert "executed" in events


def test_mcp_gateway_denial_does_not_call_remote_tool(tmp_path):
    gateway, client, coordinator = make_gateway(tmp_path, confirm=lambda action: False)
    outcome = gateway.call_tool("write_setting", {"setting": "unsafe"})
    assert outcome.ok is False
    assert outcome.status == "approval_denied"
    assert client.calls == []
    assert coordinator.audit_log.verify_chain() is True
    assert "denied" in [item["event_type"] for item in coordinator.audit_log.list_events()]


def test_mcp_gateway_rejects_unregistered_gateway_operation(tmp_path):
    gateway, client, _ = make_gateway(tmp_path)
    try:
        gateway.runtime.execute("arbitrary_operation", {})
    except ValueError as exc:
        assert "not registered" in str(exc)
    else:
        raise AssertionError("Unregistered operation should be rejected.")
    assert client.calls == []
