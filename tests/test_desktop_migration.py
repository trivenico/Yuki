import sqlite3
import tempfile
import unittest
from pathlib import Path

from ui.desktop.migration import MigrationError, migrate_data, preview_migration


class DesktopMigrationTests(unittest.TestCase):
    def test_preview_is_read_only_and_lists_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "old"
            source.mkdir()
            (source / "notes.json").write_text('{"hello": true}', encoding="utf-8")
            destination = root / "new"
            preview = preview_migration(source, destination)
            self.assertEqual(preview.files, ("notes.json",))
            self.assertFalse(destination.exists())
            self.assertTrue((source / "notes.json").exists())

    def test_migration_copies_files_and_keeps_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "old"
            source.mkdir()
            (source / "notes.json").write_text('{"hello": true}', encoding="utf-8")
            destination = root / "new"
            manifest = migrate_data(source, destination)
            self.assertEqual(manifest["files"][0]["path"], "notes.json")
            self.assertEqual(
                (destination / "notes.json").read_text(encoding="utf-8"),
                '{"hello": true}',
            )
            self.assertTrue((source / "notes.json").exists())
            self.assertTrue((destination / "migration-manifest.json").exists())

    def test_sqlite_database_uses_consistent_backup_and_integrity_check(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "old"
            source.mkdir()
            db = sqlite3.connect(source / "tasks.sqlite3")
            db.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, name TEXT)")
            db.execute("INSERT INTO tasks(name) VALUES ('keep me')")
            db.commit()
            db.close()
            destination = root / "new"
            migrate_data(source, destination)
            copied = sqlite3.connect(destination / "tasks.sqlite3")
            self.assertEqual(copied.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(copied.execute("SELECT name FROM tasks").fetchone()[0], "keep me")
            copied.close()

    def test_nonempty_destination_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "old"
            source.mkdir()
            (source / "a.txt").write_text("source")
            destination = root / "new"
            destination.mkdir()
            (destination / "keep.txt").write_text("destination")
            with self.assertRaises(MigrationError):
                migrate_data(source, destination)
            self.assertEqual((destination / "keep.txt").read_text(), "destination")

    def test_symlink_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "old"
            source.mkdir()
            outside = root / "outside.txt"
            outside.write_text("do not follow")
            try:
                (source / "link.txt").symlink_to(outside)
            except OSError:
                self.skipTest("symlinks unavailable")
            with self.assertRaises(MigrationError):
                preview_migration(source, root / "new")


if __name__ == "__main__":
    unittest.main()
