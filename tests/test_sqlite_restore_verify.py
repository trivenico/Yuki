import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from core.backup_inventory import SQLiteDatabaseEntry
from core.sqlite_backup_set import create_sqlite_backup_set
from core.sqlite_restore_verify import (
    SQLiteRestoreVerificationError,
    restore_and_verify_sqlite_backup_set,
)


class SQLiteRestoreVerificationTests(unittest.TestCase):
    def _db(self, path, value):
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE records(value TEXT NOT NULL)")
            db.execute("INSERT INTO records VALUES (?)", (value,))

    def _fixture(self):
        temporary = tempfile.TemporaryDirectory()
        base = Path(temporary.name)
        workspace = base / "workspace"
        workspace.mkdir()
        backup_root = base / "backups"
        backup_root.mkdir(mode=0o700)
        restore_root = base / "restores"
        restore_root.mkdir(mode=0o700)
        if os.name == "posix":
            os.chmod(backup_root, 0o700)
            os.chmod(restore_root, 0o700)
        source = workspace / "auth.sqlite3"
        self._db(source, "production-value")
        entries = (SQLiteDatabaseEntry("authentication", source),)
        backup_set = create_sqlite_backup_set(entries, backup_root, workspace_root=workspace)
        return temporary, workspace, source, entries, backup_set, restore_root

    def _validator(self, expected):
        def validate(paths):
            if set(paths) != {"authentication"}:
                return False
            with sqlite3.connect("file:" + str(paths["authentication"]) + "?mode=ro", uri=True) as db:
                return db.execute("SELECT value FROM records").fetchone()[0] == expected
        return validate

    def test_restores_into_new_isolated_directory_and_writes_report(self):
        temporary, workspace, source, entries, backup_set, restore_root = self._fixture()
        with temporary:
            restored = restore_and_verify_sqlite_backup_set(
                backup_set, entries, restore_root, workspace_root=workspace,
                application_validator=self._validator("production-value"),
            )
            self.assertTrue(restored.name.startswith("restore-"))
            self.assertTrue((restored / "authentication.sqlite3").is_file())
            report = json.loads((restored / "restore-report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["sqlite_integrity"], "ok")
            self.assertEqual(report["application_validation"], "passed")
            with sqlite3.connect(source) as db:
                self.assertEqual(db.execute("SELECT value FROM records").fetchone()[0], "production-value")

    def test_application_validation_failure_removes_staging_and_publishes_nothing(self):
        temporary, workspace, source, entries, backup_set, restore_root = self._fixture()
        with temporary:
            with self.assertRaisesRegex(SQLiteRestoreVerificationError, "application_validation_rejected_restore"):
                restore_and_verify_sqlite_backup_set(
                    backup_set, entries, restore_root, workspace_root=workspace,
                    application_validator=lambda paths: False,
                )
            self.assertEqual(list(restore_root.iterdir()), [])
            with sqlite3.connect(source) as db:
                self.assertEqual(db.execute("SELECT value FROM records").fetchone()[0], "production-value")

    def test_validator_exception_fails_closed(self):
        temporary, workspace, source, entries, backup_set, restore_root = self._fixture()
        with temporary:
            def fail(_paths):
                raise RuntimeError("schema check failed")
            with self.assertRaisesRegex(SQLiteRestoreVerificationError, "application_validation_failed"):
                restore_and_verify_sqlite_backup_set(
                    backup_set, entries, restore_root, workspace_root=workspace,
                    application_validator=fail,
                )
            self.assertEqual(list(restore_root.iterdir()), [])

    def test_rejects_restore_root_inside_live_workspace(self):
        temporary, workspace, source, entries, backup_set, restore_root = self._fixture()
        with temporary:
            unsafe_root = workspace / "restore"
            unsafe_root.mkdir(mode=0o700)
            with self.assertRaisesRegex(SQLiteRestoreVerificationError, "restore_root_must_be_outside_workspace"):
                restore_and_verify_sqlite_backup_set(
                    backup_set, entries, unsafe_root, workspace_root=workspace,
                    application_validator=self._validator("production-value"),
                )

    def test_requires_application_validator(self):
        temporary, workspace, source, entries, backup_set, restore_root = self._fixture()
        with temporary:
            with self.assertRaisesRegex(SQLiteRestoreVerificationError, "application_validator_required"):
                restore_and_verify_sqlite_backup_set(
                    backup_set, entries, restore_root, workspace_root=workspace,
                    application_validator=None,
                )
            self.assertEqual(list(restore_root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
