import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path

from core.approval_audit import SQLiteApprovalAuditLog


class ApprovalAuditSecurityTests(unittest.TestCase):
    def test_raw_actor_and_session_are_not_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "audit.sqlite3")
            audit = SQLiteApprovalAuditLog(path)
            audit.record_event("requested", "pending-1", "owner-private", "session-private")
            rows = audit.list_events()
            self.assertEqual(len(rows), 1)
            self.assertNotEqual(rows[0]["actor"], "owner-private")
            self.assertNotEqual(rows[0]["session_id"], "session-private")
            self.assertTrue(rows[0]["actor"].startswith("sha256:"))
            self.assertTrue(rows[0]["session_id"].startswith("sha256:"))

    def test_audit_rows_are_append_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "audit.sqlite3")
            audit = SQLiteApprovalAuditLog(path)
            audit.record_event("requested", "pending-1", "owner", "session")
            with closing(sqlite3.connect(path)) as db:
                with db:
                    with self.assertRaises(sqlite3.IntegrityError):
                        db.execute("UPDATE approval_audit SET event_type='executed' WHERE id=1")
                    with self.assertRaises(sqlite3.IntegrityError):
                        db.execute("DELETE FROM approval_audit WHERE id=1")
            self.assertEqual(audit.list_events()[0]["event_type"], "requested")

    def test_event_fields_are_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit = SQLiteApprovalAuditLog(str(Path(tmp) / "audit.sqlite3"))
            with self.assertRaises(ValueError):
                audit.record_event("x" * 65, "pending", "owner", "session")
            with self.assertRaises(ValueError):
                audit.record_event("requested", "pending", "owner", "s" * 257)

    def test_legacy_raw_identities_are_redacted_on_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "audit.sqlite3")
            with closing(sqlite3.connect(path)) as db:
                with db:
                    db.execute("""CREATE TABLE approval_audit (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        event_type TEXT NOT NULL,
                        pending_id TEXT NOT NULL,
                        actor TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )""")
                    db.execute("INSERT INTO approval_audit(event_type,pending_id,actor,session_id,created_at) VALUES(?,?,?,?,?)",
                               ("requested", "pending-1", "old-owner", "old-session", "2026-01-01T00:00:00+00:00"))
            audit = SQLiteApprovalAuditLog(path)
            row = audit.list_events()[0]
            self.assertNotEqual(row["actor"], "old-owner")
            self.assertNotEqual(row["session_id"], "old-session")
            # Migration runs before append-only triggers are installed.
            self.assertTrue(row["actor"].startswith("sha256:"))
            self.assertTrue(row["session_id"].startswith("sha256:"))


if __name__ == "__main__":
    unittest.main()
