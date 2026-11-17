import sqlite3
import tempfile
import unittest
from pathlib import Path

from core.sqlite_recovery import build_sqlite_recovery_report, inspect_sqlite_store


class SQLiteRecoveryReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_missing_store_is_reported_as_first_run_not_corruption(self):
        result = inspect_sqlite_store("conversation", self.root / "new.sqlite3")
        self.assertEqual(result["status"], "missing")

    def test_healthy_store_passes_read_only_check(self):
        path = self.root / "healthy.sqlite3"
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE sample (value TEXT)")
        result = inspect_sqlite_store("conversation", path)
        self.assertEqual(result["status"], "healthy")

    def test_corrupt_store_is_reported_without_modifying_bytes(self):
        path = self.root / "corrupt.sqlite3"
        original = b"not a database; preserve me"
        path.write_bytes(original)
        result = inspect_sqlite_store("conversation", path)
        self.assertEqual(result["status"], "corrupt_or_unreadable")
        self.assertEqual(path.read_bytes(), original)

    def test_existing_empty_store_blocks_startup(self):
        path = self.root / "empty.sqlite3"
        path.touch()
        report = build_sqlite_recovery_report({"auth": path})
        self.assertFalse(report["startup_safe"])
        self.assertEqual(report["stores"][0]["status"], "empty")

    def test_symlink_is_reported_unsafe(self):
        target = self.root / "target.sqlite3"
        target.write_bytes(b"keep")
        link = self.root / "linked.sqlite3"
        link.symlink_to(target)
        result = inspect_sqlite_store("auth", link)
        self.assertEqual(result["status"], "unsafe")
        self.assertEqual(target.read_bytes(), b"keep")

    def test_report_does_not_disclose_database_paths(self):
        path = self.root / "private-name.sqlite3"
        path.write_bytes(b"corrupt")
        report = build_sqlite_recovery_report({"auth": path})
        self.assertNotIn(str(path), repr(report))
        self.assertFalse(report["startup_safe"])


if __await__:
    pass


if __name__ == "__main__":
    unittest.main()
