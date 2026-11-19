import sqlite3
import tempfile
import unittest
from pathlib import Path

from core.backup_inventory import BackupInventoryError, build_web_sqlite_inventory


class BackupInventoryTests(unittest.TestCase):
    def _workspace(self, root, names):
        workspace = root / "workspace"
        workspace.mkdir()
        for name in names:
            with sqlite3.connect(workspace / name) as db:
                db.execute("CREATE TABLE records(value TEXT)")
        return workspace

    def test_lists_only_explicit_web_runtime_databases(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = self._workspace(root, [
                "auth.sqlite3", "inference.sqlite3",
                "income-opportunities.sqlite3", "conversations.sqlite3",
                "unrelated-cache.sqlite3",
            ])
            entries = build_web_sqlite_inventory({"workspace": str(workspace)})
            self.assertEqual(
                [(entry.name, entry.path.name) for entry in entries],
                [
                    ("authentication", "auth.sqlite3"),
                    ("conversations", "conversations.sqlite3"),
                    ("income_opportunities", "income-opportunities.sqlite3"),
                    ("inference_and_device_pairing", "inference.sqlite3"),
                ],
            )
            self.assertNotIn("unrelated-cache.sqlite3", [entry.path.name for entry in entries])

    def test_postgres_backend_does_not_mislabel_conversations_as_sqlite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = self._workspace(root, [
                "auth.sqlite3", "inference.sqlite3", "income-opportunities.sqlite3",
            ])
            entries = build_web_sqlite_inventory({
                "workspace": str(workspace), "storage_backend": "postgres",
            })
            self.assertNotIn("conversations", [entry.name for entry in entries])

    def test_requires_explicit_additional_database_registration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = self._workspace(root, [
                "auth.sqlite3", "inference.sqlite3", "income-opportunities.sqlite3",
            ])
            external = root / "workforce.sqlite3"
            with sqlite3.connect(external) as db:
                db.execute("CREATE TABLE tasks(value TEXT)")
            entries = build_web_sqlite_inventory(
                {"workspace": str(workspace)},
                additional_databases={"workforce": external},
            )
            self.assertIn(external.resolve(), [entry.path for entry in entries])

    def test_rejects_missing_database_and_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = self._workspace(root, [
                "auth.sqlite3", "inference.sqlite3", "income-opportunities.sqlite3",
            ])
            with self.assertRaisesRegex(BackupInventoryError, "database_file_missing"):
                build_web_sqlite_inventory({"workspace": str(workspace)})
            (workspace / "conversations.sqlite3").symlink_to(workspace / "auth.sqlite3")
            with self.assertRaisesRegex(BackupInventoryError, "database_symlink_not_allowed"):
                build_web_sqlite_inventory({"workspace": str(workspace)})

    def test_rejects_duplicate_paths_and_invalid_names(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = self._workspace(root, [
                "auth.sqlite3", "inference.sqlite3", "income-opportunities.sqlite3",
            ])
            with self.assertRaisesRegex(BackupInventoryError, "duplicate_database_path"):
                build_web_sqlite_inventory(
                    {"workspace": str(workspace)},
                    additional_databases={"extra": workspace / "auth.sqlite3"},
                )
            with self.assertRaisesRegex(BackupInventoryError, "invalid_additional_database_name"):
                build_web_sqlite_inventory(
                    {"workspace": str(workspace)},
                    additional_databases={"../outside": root / "external.sqlite3"},
                )


if __name__ == "__main__":
    unittest.main()
