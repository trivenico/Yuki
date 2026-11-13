"""Tests for the guarded-runtime to durable-approval adapter."""
import unittest

from core.approval_flow import ApprovalCoordinator
from core.approval_runtime_adapter import ApprovalRuntimeAdapter


class AuditLog:
    def __init__(self):
        self.events = []

    def append(self, **event):
        self.events.append(event)


class ApprovalRuntimeAdapterTests(unittest.TestCase):
    def setUp(self):
        self.audit = AuditLog()
        self.coordinator = ApprovalCoordinator(
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

    def test_records_owner_session_bound_pending_approval(self):
        pending_id = self.adapter("notes.write", self.action, self.context)
        self.assertTrue(pending_id)
        self.assertEqual(self.audit.events[-1]["event_type"], "requested")
        pending = self.coordinator.pending_store
        self.assertIsNone(pending)

    def test_rejects_action_for_different_tool(self):
        with self.assertRaisesRegex(ValueError, "bind its skill_id"):
            self.adapter("filesystem.delete", self.action, self.context)
        self.assertEqual(self.audit.events, [])

    def test_rejects_incomplete_authenticated_context(self):
        with self.assertRaisesRegex(ValueError, "complete authenticated"):
            self.adapter("notes.write", self.action, {"actor": "owner-a"})
        self.assertEqual(self.audit.events, [])

    def test_rejects_non_object_arguments(self):
        action = {"skill_id": "notes.write", "arguments": []}
        with self.assertRaisesRegex(ValueError, "bind its skill_id"):
            self.adapter("notes.write", action, self.context)
        self.assertEqual(self.audit.events, [])


if __name__ == "__main__":
    unittest.main()
