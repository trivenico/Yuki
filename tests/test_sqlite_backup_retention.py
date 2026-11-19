import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from core.backup_inventory import SQLiteDatabaseEntry
from core.sqlite_backup_retention import (
    SQLiteBackupRetentionError,
    plan_sqlite_backup_retention,
    prune_sqlite_backup_sets,
)
from core.sqlite_backup_set import create_sqlite_backup_set


class SQLiteBackupRetentionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        self.workspace = base / "workspace"
        self.workspace.mkdir()
        self.backup_root = base / "backups"
        self.backup_root.mkdir(mode=0o700)
        if os.name == "posix":
            os.chmod(self.backup_root, 0o700)
        source = self.workspace / "auth.sqlite3"
        with sqlite3.connect(source) as db:
            db.execute("CREATE TABLE records(value TEXT NOT NULL)")
            db.execute("INSERT INTO records VALUES ('source')")
        self.entries = (SQLiteDatabaseEntry("authentication", source),)

    def tearDown(self):
        self.temporary.cleanup()

    def _backup(self, ordinal):
        path = create_sqlite_backup_set(
            self.entries, self.backup_root, workspace_root=self.workspace
        )
        manifest_path = path / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["created_at"] = datetime(2026, 1, ordinal, tzinfo=timezone.utc).isoformat()
        manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
        if os.name == "posix":
            os.chmod(manifest_path, 0o600)
        return path

    def _plan(self, keep_last=1, pinned=()):
        return plan_sqlite_backup_retention(
            self.backup_root,
            self.entries,
            workspace_root=self.workspace,
            keep_last=keep_last,
            pinned_set_ids=pinned,
        )

    def test_keeps_newest_and_reports_older_as_dry_run_candidate(self):
        oldest = self._backup(1)
        newest = self._backup(2)
        before = {p.name: sorted(child.name for child in p.iterdir()) for p in self.backup_root.iterdir()}
        plan = self._plan(keep_last=1)
        after = {p.name: sorted(child.name for child in p.iterdir()) for p in self.backup_root.iterdir()}
        self.assertEqual(plan.verified_set_ids, (newest.name, oldest.name))
        self.assertEqual(plan.retained_set_ids, (newest.name,))
        self.assertEqual(plan.eligible_set_ids, (oldest.name,))
        self.assertEqual(before, after)
        self.assertTrue(oldest.exists())
        self.assertTrue(newest.exists())

    def test_pinned_older_set_is_retained(self):
        older = self._backup(1)
        newer = self._backup(2)
        plan = self._plan(keep_last=1, pinned=(older.name,))
        self.assertEqual(set(plan.retained_set_ids), {older.name, newer.name})
        self.assertEqual(plan.eligible_set_ids, ())

    def test_corrupt_set_aborts_entire_plan(self):
        good = self._backup(1)
        bad = self._backup(2)
        with (bad / "authentication.sqlite3").open("ab") as output:
            output.write(b"tamper")
        with self.assertRaisesRegex(SQLiteBackupRetentionError, "backup_set_verification_failed"):
            self._plan()
        self.assertTrue(good.exists())
        self.assertTrue(bad.exists())

    def test_unexpected_root_entry_aborts_plan(self):
        self._backup(1)
        (self.backup_root / "notes.txt").write_text("not a backup", encoding="utf-8")
        with self.assertRaisesRegex(SQLiteBackupRetentionError, "unexpected_backup_root_entry"):
            self._plan()

    def test_rejects_bool_keep_last(self):
        with self.assertRaisesRegex(SQLiteBackupRetentionError, "keep_last_must_be_positive_integer"):
            self._plan(keep_last=True)

    def test_rejects_missing_pinned_set(self):
        self._backup(1)
        with self.assertRaisesRegex(SQLiteBackupRetentionError, "pinned_backup_set_missing"):
            self._plan(pinned=("0" * 32,))


    def test_pruning_requires_explicit_approval(self):
        old = self._backup(1)
        self._backup(2)
        with self.assertRaisesRegex(SQLiteBackupRetentionError, "explicit_approval_required"):
            prune_sqlite_backup_sets(
                self.backup_root, self.entries, workspace_root=self.workspace,
                keep_last=1, approved_set_ids=(old.name,), explicit_approval=False,
            )
        self.assertTrue(old.exists())

    def test_prunes_only_explicitly_approved_eligible_set(self):
        old = self._backup(1)
        newest = self._backup(2)
        removed = prune_sqlite_backup_sets(
            self.backup_root, self.entries, workspace_root=self.workspace,
            keep_last=1, approved_set_ids=(old.name,), explicit_approval=True,
        )
        self.assertEqual(removed, (old.name,))
        self.assertFalse(old.exists())
        self.assertTrue(newest.exists())
        plan = self._plan(keep_last=1)
        self.assertEqual(plan.retained_set_ids, (newest.name,))
        self.assertEqual(plan.eligible_set_ids, ())

    def test_cannot_prune_last_known_good(self):
        only = self._backup(1)
        with self.assertRaisesRegex(SQLiteBackupRetentionError, "approved_set_not_eligible"):
            prune_sqlite_backup_sets(
                self.backup_root, self.entries, workspace_root=self.workspace,
                keep_last=1, approved_set_ids=(only.name,), explicit_approval=True,
            )
        self.assertTrue(only.exists())

    def test_cannot_prune_pinned_set(self):
        pinned = self._backup(1)
        self._backup(2)
        with self.assertRaisesRegex(SQLiteBackupRetentionError, "approved_set_not_eligible"):
            prune_sqlite_backup_sets(
                self.backup_root, self.entries, workspace_root=self.workspace,
                keep_last=1, pinned_set_ids=(pinned.name,),
                approved_set_ids=(pinned.name,), explicit_approval=True,
            )
        self.assertTrue(pinned.exists())


if __name__ == "__main__":
    unittest.main()
