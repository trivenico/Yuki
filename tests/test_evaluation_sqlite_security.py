"""Filesystem hardening tests for persistent evaluation SQLite stores."""
import os
from pathlib import Path
import stat
import tempfile
import unittest

from evaluation.benchmark_registry import BenchmarkRegistry
from evaluation.run_store import EvaluationRunStore
from core.sqlite_file_security import SQLitePathSecurityError


class EvaluationSQLiteSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    @unittest.skipUnless(os.name == "posix", "POSIX file modes are platform-specific")
    def test_run_store_database_is_owner_only(self):
        path = self.root / "runs.sqlite3"
        EvaluationRunStore(path)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    @unittest.skipUnless(os.name == "posix", "POSIX file modes are platform-specific")
    def test_benchmark_registry_database_is_owner_only(self):
        path = self.root / "benchmarks.sqlite3"
        BenchmarkRegistry(database_path=path)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_run_store_rejects_final_component_symlink(self):
        target = self.root / "target.sqlite3"
        target.touch()
        link = self.root / "runs.sqlite3"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        with self.assertRaises(SQLitePathSecurityError):
            EvaluationRunStore(link)

    def test_benchmark_registry_rejects_final_component_symlink(self):
        target = self.root / "target.sqlite3"
        target.touch()
        link = self.root / "benchmarks.sqlite3"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        with self.assertRaises(SQLitePathSecurityError):
            BenchmarkRegistry(database_path=link)

    def test_run_store_rejects_hard_linked_database(self):
        original = self.root / "original.sqlite3"
        original.touch()
        alias = self.root / "runs.sqlite3"
        try:
            os.link(original, alias)
        except (OSError, NotImplementedError):
            self.skipTest("hard links unavailable")
        with self.assertRaisesRegex(SQLitePathSecurityError, "hard_links"):
            EvaluationRunStore(alias)

    def test_benchmark_registry_rejects_hard_linked_database(self):
        original = self.root / "original.sqlite3"
        original.touch()
        alias = self.root / "benchmarks.sqlite3"
        try:
            os.link(original, alias)
        except (OSError, NotImplementedError):
            self.skipTest("hard links unavailable")
        with self.assertRaisesRegex(SQLitePathSecurityError, "hard_links"):
            BenchmarkRegistry(database_path=alias)

    def test_both_stores_reopen_existing_private_databases(self):
        run_path = self.root / "runs.sqlite3"
        benchmark_path = self.root / "benchmarks.sqlite3"
        EvaluationRunStore(run_path)
        BenchmarkRegistry(database_path=benchmark_path)
        self.assertTrue(run_path.is_file())
        self.assertTrue(benchmark_path.is_file())
        self.assertEqual(stat.S_ISREG(run_path.stat().st_mode), True)
        self.assertEqual(stat.S_ISREG(benchmark_path.stat().st_mode), True)


if __name__ == "__main__":
    unittest.main()
