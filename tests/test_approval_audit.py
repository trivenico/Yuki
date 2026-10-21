"""Regression tests for the durable approval audit log."""
import tempfile
import unittest
from pathlib import Path

from core.approval_audit import SQLiteApprovalAuditLog


class SQLiteApprovalAuditLogTests(unittest.TestCase):
    def test_events_persist_across_instances_and_exclude_action_payloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "audit.sqlite3")
            first = SQLiteApprovalAuditLog(path)
            first.append(
                event_type="requested",
                pending_id="pending-1",
                actor="owner",
                skill_id="notes.write",
            )
            second = SQLiteApprovalAuditLog(path)
            events = second.list_events()
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["event_type"], "requested")
            self.assertEqual(events[0]["pending_id"], "pending-1")
            self.assertNotIn("arguments", events[0])
            self.assertNotIn("action", events[0])

    def test_rejects_unknown_event_and_invalid_identifiers(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit = SQLiteApprovalAuditLog(str(Path(tmp) / "audit.sqlite3"))
            with self.assertRaises(ValueError):
                audit.append(event_type="payload", pending_id="p", actor="owner", skill_id="x")
            with self.assertRaises(ValueError):
                audit.append(event_type="requested", pending_id="", actor="owner", skill_id="x")

    def test_list_limit_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit = SQLiteApprovalAuditLog(str(Path(tmp) / "audit.sqlite3"))
            for limit in (0, -1, 10001, True):
                with self.assertRaises(ValueError):
                    audit.list_events(limit=limit)


if __name__ == "__main__":
    unittest.main()
