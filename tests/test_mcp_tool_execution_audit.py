"""Tests for the MCP lifecycle adapter over the shared approval audit chain."""
import tempfile
import unittest
from pathlib import Path

from core.approval_audit import SQLiteApprovalAuditLog
from core.mcp_tool_execution_audit import MCPToolExecutionAuditor


class MCPToolExecutionAuditorTests(unittest.TestCase):
    def test_policy_and_execution_events_share_hash_chain_without_payloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "audit.sqlite3")
            log = SQLiteApprovalAuditLog(path)
            audit = MCPToolExecutionAuditor(
                log, owner_id="owner", provider_id="provider", account_id="account"
            )
            common = {
                "owner_id": "owner", "provider_id": "provider", "account_id": "account",
                "tool_name": "submit_proposal", "execution_id": "a" * 32,
            }
            for event in ("requested", "owner_approved", "execution_started", "executed"):
                audit({**common, "event": event, "arguments": {"secret": "must-not-persist"}})
            events = log.list_events()
            self.assertEqual(
                [event["event_type"] for event in reversed(events)],
                ["requested", "approved", "execution_started", "executed"],
            )
            self.assertTrue(log.verify_chain())
            self.assertTrue(all(event["skill_id"] == "mcp:submit_proposal" for event in events))
            self.assertNotIn("arguments", events[0])
            with open(path, "rb") as handle:
                raw = handle.read()
            self.assertNotIn(b"must-not-persist", raw)

    def test_identity_mismatch_and_unknown_event_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = SQLiteApprovalAuditLog(str(Path(tmp) / "audit.sqlite3"))
            audit = MCPToolExecutionAuditor(
                log, owner_id="owner", provider_id="provider", account_id="account"
            )
            event = {
                "owner_id": "other", "provider_id": "provider", "account_id": "account",
                "tool_name": "read_profile", "execution_id": "b" * 32, "event": "requested",
            }
            with self.assertRaisesRegex(ValueError, "identity_mismatch"):
                audit(event)
            event["owner_id"] = "owner"
            event["event"] = "raw_arguments"
            with self.assertRaisesRegex(ValueError, "unsupported_mcp_audit_event"):
                audit(event)
            self.assertEqual(log.list_events(), [])

    def test_long_tool_name_is_stably_fingerprinted_for_bounded_audit_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = SQLiteApprovalAuditLog(str(Path(tmp) / "audit.sqlite3"))
            audit = MCPToolExecutionAuditor(
                log, owner_id="owner", provider_id="provider", account_id="account"
            )
            audit({
                "owner_id": "owner", "provider_id": "provider", "account_id": "account",
                "tool_name": "x" * 128, "execution_id": "c" * 32, "event": "execution_started",
            })
            self.assertTrue(log.list_events()[0]["skill_id"].startswith("mcp:sha256:"))
            self.assertTrue(log.verify_chain())


if __name__ == "__main__":
    unittest.main()
