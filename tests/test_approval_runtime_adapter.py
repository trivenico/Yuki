"""Tests for the guarded-runtime to durable-approval adapter."""
import tempfile
import unittest
from pathlib import Path

from core.approval_flow import ApprovalCoordinator
from core.pending_approvals import SQLitePendingApprovalStore
from core.approval_runtime_adapter import ApprovalRuntimeAdapter


class AuditLog:
    def __init__(self):
        self.events = []

    def append(self, **event):
        self.events.append(event)


class ApprovalRuntimeAdapterTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = SQLitePendingApprovalStore(
            str(Path(self.directory.name) / "pending.sqlite3")
        )
        self.audit = AuditLog()
        self.coordinator = ApprovalCoordinator(
            pending_store=self.store,
            audit_log=self.audit,
            planner_resolver=lambda **_: None,
        )
        self.adapter = ApprovalRuntimeAdapter(self.coordinator)
        self.context = {
            "actor": "owner-a",
            "session_id": "session-a",
            "conversation_id": "conversation-a",
            "request_id": "request-a",
        }
        self.action = {
            "skill_id": "notes.write",
            "arguments": {"text": "owner-approved content"},
        }

    def tearDown(self):
        self.directory.cleanup()

    def test_records_owner_session_bound_pending_approval(self):
        pending_id = self.adapter("notes.write", self.action, self.context)
        self.assertTrue(pending_id)
        self.assertEqual(self.audit.events[-1]["event_type"], "requested")
        stored = self.store.get_for_identity(
            pending_id, actor="owner-a", session_id="session-a"
        )
        self.assertIsNotNone(stored)
        self.assertEqual(stored.action["tool_name"], "notes.write")
        self.assertEqual(stored.action["arguments"]["text"], "owner-approved content")

    def test_request_retry_reuses_pending_id_without_duplicate_audit(self):
        first = self.adapter("notes.write", self.action, self.context)
        second = self.adapter("notes.write", self.action, self.context)
        self.assertEqual(first, second)
        self.assertEqual(
            [event["event_type"] for event in self.audit.events],
            ["requested"],
        )

    def test_request_id_cannot_be_reused_for_a_different_action(self):
        self.adapter("notes.write", self.action, self.context)
        changed = {
            "skill_id": "notes.write",
            "arguments": {"text": "different payload"},
        }
        with self.assertRaisesRegex(ValueError, "different action"):
            self.adapter("notes.write", changed, self.context)

    def test_persists_tool_name_separately_from_skill_id(self):
        action = {"skill_id": "notes.create", "arguments": {"text": "safe"}}
        pending_id = self.adapter("notes.write", action, self.context)
        self.assertTrue(pending_id)
        stored = self.store.get_for_identity(
            pending_id, actor="owner-a", session_id="session-a"
        )
        self.assertEqual(stored.action["skill_id"], "notes.create")
        self.assertEqual(stored.action["tool_name"], "notes.write")

    def test_rejects_conflicting_declared_tool_name(self):
        action = {
            "skill_id": "notes.create",
            "tool_name": "filesystem.delete",
            "arguments": {},
        }
        with self.assertRaisesRegex(ValueError, "valid skill and arguments"):
            self.adapter("notes.write", action, self.context)
        self.assertEqual(self.audit.events, [])

    def test_rejects_incomplete_authenticated_context(self):
        with self.assertRaisesRegex(ValueError, "complete authenticated"):
            self.adapter("notes.write", self.action, {"actor": "owner-a"})
        self.assertEqual(self.audit.events, [])

    def test_rejects_non_object_arguments(self):
        action = {"skill_id": "notes.write", "arguments": []}
        with self.assertRaisesRegex(ValueError, "valid skill and arguments"):
            self.adapter("notes.write", action, self.context)
        self.assertEqual(self.audit.events, [])


if __name__ == "__main__":
    unittest.main()
