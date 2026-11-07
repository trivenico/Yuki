"""Tests for consistent, checksummed SQLite workspace backups."""
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from scripts.backup_yuki import create_backup


class BackupYukiTests(unittest.TestCase):
    def test_backup_contains_consistent_database_and_checksum_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            database = workspace / "nested" / "memory.sqlite3"
            database.parent.mkdir()
            with closing(sqlite3.connect(database)) as db, db:
                db.execute("CREATE TABLE memories (value TEXT)")
                db.execute("INSERT INTO memories VALUES ('persistent memory')")
            output = root / "backup"
            manifest_path = create_backup(workspace, output)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            entry = manifest["databases"][0]
            backup_db = output / entry["backup_path"]
            self.assertEqual(manifest["database_count"], 1)
            self.assertEqual(entry["path"], "nested/memory.sqlite3")
            self.assertEqual(entry["sha256"], __import__("hashlib").sha256(backup_db.read_bytes()).hexdigest())
            with closing(sqlite3.connect(backup_db)) as db, db:
                self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(db.execute("SELECT value FROM memories").fetchone()[0], "persistent memory")

    def test_backup_refuses_workspace_with_incomplete_restore_marker(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            with closing(sqlite3.connect(workspace / "app.db")) as db, db:
                db.execute("CREATE TABLE sample (id INTEGER)")
            (workspace / ".yuki-restore-incomplete").write_text("inspect", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "incomplete restore"):
                create_backup(workspace, root / "backup")
            self.assertFalse((root / "backup").exists())

    def test_backup_refuses_to_write_inside_workspace(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            workspace.mkdir()
            with self.assertRaisesRegex(ValueError, "outside the live workspace"):
                create_backup(workspace, workspace / "backup")

    def test_backup_requires_existing_workspace_and_database(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ValueError, "does not exist"):
                create_backup(root / "missing", root / "backup")
            workspace = root / "empty"
            workspace.mkdir()
            with self.assertRaisesRegex(ValueError, "No SQLite databases"):
                create_backup(workspace, root / "empty-backup")

    def test_backup_never_overwrites_existing_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            with closing(sqlite3.connect(workspace / "app.db")) as db, db:
                db.execute("CREATE TABLE sample (id INTEGER)")
            output = root / "existing"
            output.mkdir()
            with self.assertRaises(FileExistsError):
                create_backup(workspace, output)

    def test_backup_refuses_database_symlink_to_outside_workspace(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            outside = root / "private.db"
            with closing(sqlite3.connect(outside)) as db, db:
                db.execute("CREATE TABLE secrets (value TEXT)")
                db.execute("INSERT INTO secrets VALUES ('must not be copied')")
            link = workspace / "linked.db"
            try:
                link.symlink_to(outside)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"Symlink creation is unavailable: {exc}")
            with self.assertRaisesRegex(ValueError, "symbolic-link database"):
                create_backup(workspace, root / "backup")
            self.assertFalse((root / "backup").exists())


if __name__ == "__main__":
    unittest.main()
