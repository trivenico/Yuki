import tempfile
import unittest
from pathlib import Path

from core.approval_audit import SQLiteApprovalAuditLog


class SQLiteApprovalAuditLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "audit.sqlite3")
        self.audit = SQLiteApprovalAuditLog(self.path)
        self.action = {"skill_id": "notes.write", "arguments": {"text": "private note"}}

    def test_records_hashes_not_raw_identity_or_action_arguments(self):
        self.audit.record(event_type="approved", skill_id="notes.write", action=self.action,
                          outcome="approved", actor="owner@example.test",
                          session_id="secret-session", pending_id="private-pending",
                          details={"risk": "low", "attempt": 1})
        events = self.audit.list_events()
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertNotIn("owner@example.test", str(event))
        self.assertNotIn("secret-session", str(event))
        self.assertNotIn("private note", str(event))
        self.assertNotIn("private-pending", str(event))
        self.assertEqual(event["skill_id"], "notes.write")
        self.assertEqual(event["outcome"], "approved")

    def test_rejects_arbitrary_string_details_that_could_leak_prompts(self):
        with self.assertRaises(ValueError):
            self.audit.record(event_type="requested", skill_id="notes.read",
                              action={"skill_id": "notes.read"}, outcome="pending",
                              details={"prompt": "do not persist this text"})

    def test_invalid_event_and_oversized_action_rejected(self):
        with self.assertRaises(ValueError):
            self.audit.record(event_type="whatever", skill_id="notes.read",
                              action={}, outcome="pending")
        with self.assertRaises(ValueError):
            self.audit.record(event_type="requested", skill_id="notes.read",
                              action={"text": "x" * 17000}, outcome="pending")

    def test_event_order_and_limit(self):
        for i in range(3):
            self.audit.record(event_type="requested", skill_id="notes.read",
                              action={"step": i}, outcome="pending", details={"step": i})
        events = self.audit.list_events(limit=2)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["details"]["step"], 2)


if __name__ == "__main__":
    unittest.main()
