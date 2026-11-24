import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from core.audit_anchor_operations import (
    AuditAnchorOperationsError,
    audit_anchor_readiness,
    create_audit_anchor_backup,
)
from core.audit_anchor_service import SQLiteAuditAnchorService
from core.authorization_audit import AuthorizationAuditCheckpoint


class AuditAnchorOperationsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.service = SQLiteAuditAnchorService(
            str(self.root / "anchor.sqlite3"),
            authorize_administrator=lambda identity: identity == "admin",
        )
        self.service.set_publisher("admin", "publisher", enabled=True)

    def tearDown(self):
        self.directory.cleanup()

    def test_readiness_reports_initialized_and_uninitialized_states(self):
        result = audit_anchor_readiness(self.service)
        self.assertEqual(result, {
            "ready": True, "reason": "ok", "checkpoint_initialized": False,
        })
        first = self.service.publish_checkpoint(
            "publisher", AuthorizationAuditCheckpoint(0, "0" * 64),
            if_match=None, if_none_match="*",
        )
        result = audit_anchor_readiness(self.service)
        self.assertTrue(result["ready"])
        self.assertTrue(result["checkpoint_initialized"])

    def test_online_backup_is_private_integral_and_contains_consistent_state(self):
        self.service.publish_checkpoint(
            "publisher", AuthorizationAuditCheckpoint(3, "a" * 64),
            if_match=None, if_none_match="*",
        )
        destination = self.root / "backups" / "anchor-backup.sqlite3"
        result = create_audit_anchor_backup(self.service, str(destination))
        self.assertEqual(result.checkpoint_event_id, 3)
        self.assertEqual(len(result.sha256), 64)
        self.assertGreater(result.size_bytes, 0)
        if os.name == "posix":
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
        with sqlite3.connect(str(destination)) as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone(), ("ok",))
            self.assertEqual(
                db.execute("SELECT event_id FROM anchor_checkpoint").fetchone(), (3,)
            )
            self.assertEqual(
                db.execute("SELECT active FROM anchor_publishers WHERE identity='publisher'").fetchone(),
                (1,),
            )

    def test_backup_rejects_source_as_destination(self):
        with self.assertRaisesRegex(ValueError, "must_differ"):
            create_audit_anchor_backup(self.service, self.service.database_path)

    def test_readiness_fails_closed_for_schema_damage(self):
        with sqlite3.connect(self.service.database_path) as db:
            db.execute("DROP TABLE anchor_admin_audit")
        result = audit_anchor_readiness(self.service)
        self.assertFalse(result["ready"])
        self.assertEqual(result["reason"], "database_unavailable_or_invalid")

    def test_failed_backup_does_not_replace_existing_destination(self):
        destination = self.root / "backup.sqlite3"
        destination.write_bytes(b"previous-backup")
        with sqlite3.connect(self.service.database_path) as db:
            db.execute("DROP TABLE anchor_checkpoint")
        with self.assertRaises(AuditAnchorOperationsError):
            create_audit_anchor_backup(self.service, str(destination))
        self.assertEqual(destination.read_bytes(), b"previous-backup")


if __name__ == "__main__":
    unittest.main()
