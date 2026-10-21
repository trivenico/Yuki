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
            self.assertEqual(events[0]["pending_id_hash"], SQLiteApprovalAuditLog._digest("pending-1"))
            self.assertNotIn("pending_id", events[0])
            self.assertNotIn("arguments", events[0])
            self.assertNotIn("action", events[0])

    def test_rejects_unknown_event_and_invalid_identifiers(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit = SQLiteApprovalAuditLog(str(Path(tmp) / "audit.sqlite3"))
            with self.assertRaises(ValueError):
                audit.append(event_type="payload", pending_id="p", actor="owner", skill_id="x")
            with self.assertRaises(ValueError):
                audit.append(event_type="requested", pending_id="", actor="owner", skill_id="x")

    def test_audit_chain_verifies_and_detects_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "audit.sqlite3")
            audit = SQLiteApprovalAuditLog(path)
            audit.append(event_type="requested", pending_id="pending-1", actor="owner-secret", skill_id="notes.write")
            audit.append(event_type="approved", pending_id="pending-1", actor="owner-secret", skill_id="notes.write")
            self.assertTrue(audit.verify_chain())
            import sqlite3
            db = sqlite3.connect(path)
            try:
                db.execute("UPDATE approval_audit_events SET skill_id = 'tampered' WHERE event_id = 1")
                db.commit()
            finally:
                db.close()
            self.assertFalse(audit.verify_chain())

    def test_pending_bearer_token_is_not_persisted_in_plaintext(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "audit.sqlite3")
            audit = SQLiteApprovalAuditLog(path)
            audit.append(event_type="requested", pending_id="secret-pending-token", actor="owner", skill_id="notes.write")
            with open(path, "rb") as handle:
                self.assertNotIn(b"secret-pending-token", handle.read())
            self.assertTrue(audit.verify_chain())

    def test_actor_identifier_is_not_persisted_in_plaintext(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "audit.sqlite3")
            audit = SQLiteApprovalAuditLog(path)
            audit.append(event_type="requested", pending_id="pending-1", actor="secret-actor-123", skill_id="notes.write")
            with open(path, "rb") as handle:
                self.assertNotIn(b"secret-actor-123", handle.read())

    def test_list_limit_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit = SQLiteApprovalAuditLog(str(Path(tmp) / "audit.sqlite3"))
            for limit in (0, -1, 10001, True):
                with self.assertRaises(ValueError):
                    audit.list_events(limit=limit)


if __name__ == "__main__":
    unittest.main()
