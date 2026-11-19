import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from automation.authorized_read_tools import ToolAuthorizationError, ToolPrincipal
from core.approval_audit import SQLiteApprovalAuditLog
from core.approved_sqlite_backup_pruning import (
    ApprovedSQLiteBackupPruner,
    BackupPruneApprovalError,
)
from core.backup_inventory import SQLiteDatabaseEntry
from core.pending_approvals import SQLitePendingApprovalStore
from core.sqlite_backup_set import create_sqlite_backup_set


class ApprovedSQLiteBackupPruningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.workspace = base / "workspace"
        self.workspace.mkdir()
        self.backup_root = base / "backups"
        self.backup_root.mkdir(mode=0o700)
        if os.name == "posix":
            os.chmod(self.backup_root, 0o700)
        self.source = self.workspace / "auth.sqlite3"
        with sqlite3.connect(self.source) as db:
            db.execute("CREATE TABLE records(value TEXT NOT NULL)")
            db.execute("INSERT INTO records VALUES ('ok')")
        self.entries = (SQLiteDatabaseEntry("authentication", self.source),)
        self.pending = SQLitePendingApprovalStore(str(base / "pending.sqlite3"))
        self.audit = SQLiteApprovalAuditLog(str(base / "audit.sqlite3"))
        self.principal = ToolPrincipal("operator-1", frozenset({"backup:prune"}))
        self.approval_calls = []

    def backup(self):
        return create_sqlite_backup_set(
            self.entries, self.backup_root, workspace_root=self.workspace
        )

    def service(self, approve=True, audit_log=None):
        return ApprovedSQLiteBackupPruner(
            pending_store=self.pending,
            audit_log=audit_log or self.audit,
            trusted_principal=lambda p: p.actor_id == "operator-1",
            approve_operation=lambda actor, pending_id, digest: (
                self.approval_calls.append((actor, pending_id, digest)) or approve
            ),
        )

    def request(self, service, old):
        return service.approve(
            principal=self.principal, session_id="session-1",
            backup_root=self.backup_root, expected_entries=self.entries,
            workspace_root=self.workspace, keep_last=1,
            approved_set_ids=(old.name,),
        )

    def execute(self, service, approval, session="session-1"):
        return service.execute(
            principal=self.principal, session_id=session, approval=approval,
            backup_root=self.backup_root, expected_entries=self.entries,
            workspace_root=self.workspace, keep_last=1,
        )

    def test_approved_exact_set_is_pruned_and_audited(self):
        old = self.backup()
        newest = self.backup()
        service = self.service()
        approval = self.request(service, old)
        self.assertEqual(self.execute(service, approval), (old.name,))
        self.assertFalse(old.exists())
        self.assertTrue(newest.exists())
        events = [e["event_type"] for e in reversed(self.audit.list_events())]
        self.assertEqual(events, ["requested", "approved", "execution_started", "executed"])
        self.assertTrue(self.audit.verify_chain())

    def test_missing_permission_and_untrusted_principal_rejected(self):
        old = self.backup()
        service = self.service()
        no_permission = ToolPrincipal("operator-1", frozenset())
        with self.assertRaises(ToolAuthorizationError):
            service.approve(
                principal=no_permission, session_id="session-1",
                backup_root=self.backup_root, expected_entries=self.entries,
                workspace_root=self.workspace, keep_last=1,
                approved_set_ids=(old.name,),
            )
        with self.assertRaises(ToolAuthorizationError):
            ApprovedSQLiteBackupPruner(
                pending_store=self.pending, audit_log=self.audit,
                trusted_principal=lambda _p: False,
                approve_operation=lambda *_: True,
            ).approve(
                principal=self.principal, session_id="session-1",
                backup_root=self.backup_root, expected_entries=self.entries,
                workspace_root=self.workspace, keep_last=1,
                approved_set_ids=(old.name,),
            )
        self.assertTrue(old.exists())

    def test_denied_approval_does_not_prune(self):
        old = self.backup()
        self.backup()
        with self.assertRaises(ToolAuthorizationError):
            self.request(self.service(approve=False), old)
        self.assertTrue(old.exists())
        self.assertEqual(self.audit.list_events(limit=1)[0]["event_type"], "denied")

    def test_approval_is_session_bound_and_single_use(self):
        old = self.backup()
        self.backup()
        service = self.service()
        approval = self.request(service, old)
        with self.assertRaises(BackupPruneApprovalError):
            self.execute(service, approval, session="other-session")
        self.assertTrue(old.exists())
        self.assertEqual(self.execute(service, approval), (old.name,))
        with self.assertRaises(BackupPruneApprovalError):
            self.execute(service, approval)
        
    def test_manifest_change_invalidates_approval(self):
        old = self.backup()
        self.backup()
        service = self.service()
        approval = self.request(service, old)
        manifest_path = old / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["created_at"] = "2026-01-01T00:00:00+00:00"
        manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
        if os.name == "posix":
            os.chmod(manifest_path, 0o600)
        with self.assertRaises(BackupPruneApprovalError):
            self.execute(service, approval)
        self.assertTrue(old.exists())

    def test_requested_audit_failure_denies_before_approval(self):
        old = self.backup()
        self.backup()
        broken_audit = SQLiteApprovalAuditLog(str(Path(self.temp.name) / "broken.sqlite3"))
        service = ApprovedSQLiteBackupPruner(
            pending_store=self.pending, audit_log=broken_audit,
            trusted_principal=lambda _p: True,
            approve_operation=lambda *_: True,
        )
        original = broken_audit.append
        broken_audit.append = lambda **_kwargs: (_ for _ in ()).throw(OSError("audit down"))
        with self.assertRaises(BackupPruneApprovalError):
            self.request(service, old)
        self.assertTrue(old.exists())
        broken_audit.append = original


if __name__ == "__main__":
    unittest.main()
