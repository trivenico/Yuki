import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from core.sqlite_backup import SQLiteBackupError, backup_sqlite_database


class SQLiteBackupTests(unittest.TestCase):
    def test_backup_includes_committed_wal_data_and_passes_integrity_check(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "live.sqlite3"
            destination = root / "backups" / "live.sqlite3"
            destination.parent.mkdir(mode=0o700)
            live = sqlite3.connect(source)
            try:
                live.execute("PRAGMA journal_mode=WAL")
                live.execute("CREATE TABLE records(value TEXT NOT NULL)")
                live.execute("INSERT INTO records VALUES ('committed-in-wal')")
                live.commit()

                backup_sqlite_database(source, destination)

                with sqlite3.connect(destination) as snapshot:
                    self.assertEqual(
                        snapshot.execute("SELECT value FROM records").fetchone()[0],
                        "committed-in-wal",
                    )
                    self.assertEqual(snapshot.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                if os.name == "posix":
                    self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
            finally:
                live.close()

    def test_failed_backup_does_not_replace_existing_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "not-a-database.sqlite3"
            source.write_text("not sqlite", encoding="utf-8")
            destination = root / "backup.sqlite3"
            destination.write_text("previous verified backup", encoding="utf-8")

            with self.assertRaises(SQLiteBackupError):
                backup_sqlite_database(source, destination)

            self.assertEqual(destination.read_text(encoding="utf-8"), "previous verified backup")

    def test_rejects_symlink_source(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            actual = root / "actual.sqlite3"
            with sqlite3.connect(actual) as db:
                db.execute("CREATE TABLE records(value TEXT)")
            link = root / "linked.sqlite3"
            try:
                link.symlink_to(actual)
            except OSError:
                self.skipTest("symlink creation not permitted")
            with self.assertRaisesRegex(SQLiteBackupError, "source_must_be_regular_file"):
                backup_sqlite_database(link, root / "backup.sqlite3")


if __name__ == "__main__":
    unittest.main()
