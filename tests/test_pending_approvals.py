"""Durable approval operation-intent persistence tests."""
import tempfile
import unittest
from pathlib import Path

from core.pending_approvals import SQLitePendingApprovalStore


class ApprovalOperationIntentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SQLitePendingApprovalStore(str(Path(self.tmp.name) / "pending.sqlite3"))
        self.item = self.store.put("mail.send", {"to": "owner@example.test"},
            {"tool_name": "mail", "skill_id": "mail.send", "arguments": {"to": "owner@example.test"}},
            actor="owner-a", session_id="session-a")
        self.store.transition(self.item.pending_id, expected_status="pending", new_status="claimed",
            actor="owner-a", session_id="session-a")
        self.store.transition(self.item.pending_id, expected_status="claimed", new_status="approved",
            actor="owner-a", session_id="session-a")

    def tearDown(self):
        self.tmp.cleanup()

    def test_operation_intent_is_stable_and_action_bound(self):
        first = self.store.ensure_operation_intent(self.item.pending_id, actor="owner-a", session_id="session-a")
        second = self.store.ensure_operation_intent(self.item.pending_id, actor="owner-a", session_id="session-a")
        self.assertEqual(first, second)
        self.assertEqual(len(first["operation_id"]), 43)
        self.assertEqual(len(first["action_digest"]), 64)
        self.assertEqual(self.store.get_operation_intent(self.item.pending_id, actor="owner-a", session_id="session-a"), first)

    def test_other_owner_or_session_cannot_read_or_create_intent(self):
        self.assertIsNone(self.store.ensure_operation_intent(self.item.pending_id, actor="owner-b", session_id="session-a"))
        self.assertIsNone(self.store.get_operation_intent(self.item.pending_id, actor="owner-a", session_id="session-b"))

    def test_intent_cannot_be_created_before_approval(self):
        other = self.store.put("files.delete", {"path": "x"},
            {"tool_name": "files", "skill_id": "files.delete", "arguments": {"path": "x"}},
            actor="owner-a", session_id="session-a")
        self.assertIsNone(self.store.ensure_operation_intent(other.pending_id, actor="owner-a", session_id="session-a"))


if __name__ == "__main__":
    unittest.main()