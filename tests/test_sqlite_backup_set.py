import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from core.backup_inventory import SQLiteDatabaseEntry
from core.sqlite_backup_set import SQLiteBackupSetError, create_sqlite_backup_set


class SQLiteBackupSetTests(unittest.TestCase):
    def _database(self, path, value):
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE records(value TEXT NOT NULL)")
            db.execute("INSERT INTO records VALUES (?)", (value,))

    def _root(self, path):
        path.mkdir(mode=0o700)
        if os.name == "posix":
            os.chmod(path, 0o700)
        return path

    def test_publishes_complete_manifest_and_verified_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            workspace = base / "workspace"
            workspace.mkdir()
            backup_root = self._root(base / "protected-backups")
            first, second = workspace / "auth.sqlite3", workspace / "jobs.sqlite3"
            self._database(first, "user-record")
            self._database(second, "task-record")

            published = create_sqlite_backup_set(
                (
                    SQLiteDatabaseEntry("authentication", first),
                    SQLiteDatabaseEntry("workforce", second),
                ),
                backup_root,
                workspace_root=workspace,
            )

            self.assertEqual(published.parent.resolve(), backup_root.resolve())
            self.assertFalse(published.name.startswith("."))
            manifest = json.loads((published / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["format_version"], 1)
            self.assertEqual(manifest["snapshot_consistency"], "per_database")
            self.assertEqual(
                [item["name"] for item in manifest["databases"]],
                ["authentication", "workforce"],
            )
            for item in manifest["databases"]:
                snapshot = published / item["filename"]
                self.assertEqual(snapshot.stat().st_size, item["size_bytes"])
                self.assertEqual(item["integrity_check"], "ok")
                with sqlite3.connect(snapshot) as db:
                    self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                    self.assertEqual(db.execute("SELECT value FROM records").fetchone()[0],
                                     "user-record" if item["name"] == "authentication" else "task-record")

    def test_failure_does_not_publish_partial_set_or_remove_existing_sets(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            workspace = base / "workspace"
            workspace.mkdir()
            backup_root = self._root(base / "protected-backups")
            source = workspace / "auth.sqlite3"
            self._database(source, "good")
            existing = backup_root / "previous-known-good"
            existing.mkdir()
            (existing / "manifest.json").write_text("{}", encoding="utf-8")
            missing = workspace / "missing.sqlite3"

            with self.assertRaises(SQLiteBackupSetError):
                create_sqlite_backup_set(
                    (
                        SQLiteDatabaseEntry("authentication", source),
                        SQLiteDatabaseEntry("workforce", missing),
                    ),
                    backup_root,
                    workspace_root=workspace,
                )

            self.assertTrue((existing / "manifest.json").is_file())
            self.assertEqual([p.name for p in backup_root.iterdir()], ["previous-known-good"])

    def test_rejects_backup_root_inside_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            backup_root = self._root(workspace / "backups")
            source = workspace / "auth.sqlite3"
            self._database(source, "value")
            with self.assertRaisesRegex(SQLiteBackupSetError, "backup_root_must_be_outside_workspace"):
                create_sqlite_backup_set(
                    (SQLiteDatabaseEntry("authentication", source),),
                    backup_root,
                    workspace_root=workspace,
                )

    def test_rejects_invalid_name_before_creating_staging_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            workspace = base / "workspace"
            workspace.mkdir()
            backup_root = self._root(base / "protected-backups")
            source = workspace / "auth.sqlite3"
            self._database(source, "value")
            with self.assertRaisesRegex(SQLiteBackupSetError, "invalid_database_name"):
                create_sqlite_backup_set(
                    (SQLiteDatabaseEntry("../escape", source),),
                    backup_root,
                    workspace_root=workspace,
                )
            self.assertEqual(list(backup_root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
