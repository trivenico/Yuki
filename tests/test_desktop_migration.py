import sqlite3
import tempfile
import unittest
from unittest.mock import patch
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

    def test_preview_rejects_reserved_manifest_name_without_overwriting_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "old"
            source.mkdir()
            original = source / "migration-manifest.json"
            original.write_text('{"user_data": "preserve"}', encoding="utf-8")
            with self.assertRaisesRegex(MigrationError, "reserved migration-manifest"):
                preview_migration(source, root / "new")
            self.assertEqual(original.read_text(encoding="utf-8"), '{"user_data": "preserve"}')
            self.assertFalse((root / "new").exists())

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

    def test_preview_enforces_file_count_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "old"
            source.mkdir()
            (source / "a.txt").write_text("a")
            (source / "b.txt").write_text("b")
            with patch("ui.desktop.migration.MAX_MIGRATION_FILES", 1):
                with self.assertRaises(MigrationError):
                    preview_migration(source, root / "new")

    def test_preview_enforces_total_size_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "old"
            source.mkdir()
            (source / "large.txt").write_bytes(b"12345")
            with patch("ui.desktop.migration.MAX_MIGRATION_BYTES", 4):
                with self.assertRaises(MigrationError):
                    preview_migration(source, root / "new")

    def test_migration_does_not_copy_stale_sqlite_sidecars(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "old"
            source.mkdir()
            db = sqlite3.connect(source / "tasks.sqlite3")
            db.execute("CREATE TABLE tasks (name TEXT)")
            db.execute("INSERT INTO tasks VALUES ('preserved')")
            db.commit()
            db.close()
            (source / "tasks.sqlite3-wal").write_bytes(b"stale-wal")
            destination = root / "new"
            migrate_data(source, destination)
            copied = sqlite3.connect(destination / "tasks.sqlite3")
            self.assertEqual(copied.execute("SELECT name FROM tasks").fetchone()[0], "preserved")
            copied.close()
            self.assertFalse((destination / "tasks.sqlite3-wal").exists())

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

    def test_symlink_destination_is_rejected_without_touching_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "old"
            source.mkdir()
            (source / "notes.txt").write_text("keep")
            real_destination = root / "real-destination"
            real_destination.mkdir()
            link = root / "new"
            try:
                link.symlink_to(real_destination, target_is_directory=True)
            except OSError:
                self.skipTest("symlinks unavailable")
            with self.assertRaises(MigrationError):
                preview_migration(source, link)
            self.assertTrue(real_destination.is_dir())

    def test_sqlite_filename_with_uri_special_characters_is_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "old"
            source.mkdir()
            filename = "tasks #1.sqlite3" if __import__("os").name == "nt" else "tasks #1?.sqlite3"
            db_path = source / filename
            db = sqlite3.connect(db_path)
            db.execute("CREATE TABLE tasks (name TEXT)")
            db.execute("INSERT INTO tasks VALUES ('safe')")
            db.commit()
            db.close()
            destination = root / "new"
            migrate_data(source, destination)
            copied = sqlite3.connect(destination / db_path.name)
            self.assertEqual(copied.execute("SELECT name FROM tasks").fetchone()[0], "safe")
            copied.close()

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
