from __future__ import annotations

import hashlib
import unittest
from unittest.mock import MagicMock, call, patch

from core import postgres_migrations as migrations


class MigrationManifestTests(unittest.TestCase):
    def test_manifest_is_contiguous_and_checksums_are_sha256(self) -> None:
        items = migrations.load_migrations()
        self.assertEqual([item.version for item in items], list(range(1, 17)))
        for item in items:
            self.assertEqual(item.checksum, hashlib.sha256(item.sql.encode()).hexdigest())
            self.assertTrue(item.sql.strip())

    def test_manifest_rejects_unregistered_sql_files(self) -> None:
        registered = migrations.MIGRATION_FILES
        with patch.object(migrations, "MIGRATION_FILES", registered[:-1]):
            with self.assertRaisesRegex(migrations.MigrationError, "manifest mismatch"):
                migrations.load_migrations()

    def test_history_rejects_checksum_drift(self) -> None:
        item = migrations.load_migrations()[0]
        with self.assertRaisesRegex(migrations.MigrationError, "history mismatch"):
            migrations._validate_history({1: (item.name, "0" * 64)}, (item,))

    def test_history_rejects_unknown_versions_and_gaps(self) -> None:
        items = migrations.load_migrations()
        with self.assertRaisesRegex(migrations.MigrationError, "unknown"):
            migrations._validate_history({99: ("future.sql", "0" * 64)}, items)
        with self.assertRaisesRegex(migrations.MigrationError, "gap"):
            migrations._validate_history(
                {1: (items[0].name, items[0].checksum), 3: (items[2].name, items[2].checksum)},
                items,
            )

    def test_history_accepts_exact_prefix(self) -> None:
        items = migrations.load_migrations()
        applied = {item.version: (item.name, item.checksum) for item in items[:4]}
        migrations._validate_history(applied, items)


class MigrationExecutionTests(unittest.TestCase):
    def test_nonempty_schema_without_ledger_is_rejected(self) -> None:
        cursor = MagicMock()
        cursor.fetchone.return_value = (None,)
        cursor.fetchall.return_value = [("existing_table",)]
        with self.assertRaisesRegex(migrations.MigrationError, "non-empty"):
            migrations._ensure_safe_initialization(cursor)

    def test_empty_schema_without_ledger_is_allowed(self) -> None:
        cursor = MagicMock()
        cursor.fetchone.return_value = (None,)
        cursor.fetchall.return_value = []
        migrations._ensure_safe_initialization(cursor)

    def test_ledger_creation_is_explicitly_apply_only(self) -> None:
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.fetchone.return_value = (None,)
        cursor.fetchall.return_value = []
        with patch.object(migrations, "load_migrations", return_value=()):
            self.assertEqual(migrations.migrate(connection, apply=False), ())
        connection.transaction.assert_not_called()

    def test_apply_failure_stops_before_later_migrations(self) -> None:
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.fetchone.side_effect = [(None,), (None,)]
        cursor.fetchall.return_value = []
        tx_cursor = connection.cursor.return_value.__enter__.return_value
        tx_cursor.execute.side_effect = [None, RuntimeError("ddl failed")]
        first = migrations.Migration(1, "0001_test.sql", "SELECT 1", "a" * 64)
        second = migrations.Migration(2, "0002_test.sql", "SELECT 2", "b" * 64)
        with patch.object(migrations, "load_migrations", return_value=(first, second)):
            with self.assertRaisesRegex(RuntimeError, "ddl failed"):
                migrations.migrate(connection, apply=True)
        self.assertEqual(tx_cursor.execute.call_count, 2)


if __name__ == "__main__":
    unittest.main()
