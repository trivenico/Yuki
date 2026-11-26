from __future__ import annotations

import os
import unittest

try:
    import psycopg
except ImportError:  # pragma: no cover - exercised in minimal developer installs
    psycopg = None

from core import postgres_migrations


DSN = os.environ.get("YUKI_TEST_POSTGRES_URL", "").strip()


@unittest.skipUnless(DSN and psycopg is not None, "requires disposable PostgreSQL integration database")
class PostgreSQLMigrationIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = psycopg.connect(DSN, autocommit=True)
        # The workflow provisions a dedicated disposable database for this suite.
        with self.connection.cursor() as cursor:
            cursor.execute("DROP SCHEMA public CASCADE")
            cursor.execute("CREATE SCHEMA public")
            cursor.execute("GRANT ALL ON SCHEMA public TO CURRENT_USER")

    def tearDown(self) -> None:
        self.connection.close()

    def test_fresh_database_applies_all_migrations_and_is_idempotent(self) -> None:
        expected = tuple(item.name for item in postgres_migrations.load_migrations())
        applied = postgres_migrations.migrate(self.connection, apply=True)
        self.assertEqual(applied, expected)
        self.assertEqual(postgres_migrations.migrate(self.connection, apply=False), ())
        self.assertEqual(postgres_migrations.migrate(self.connection, apply=True), ())
        with self.connection.cursor() as cursor:
            cursor.execute(
                f"SELECT version, name FROM {postgres_migrations.LEDGER_TABLE} ORDER BY version"
            )
            rows = cursor.fetchall()
        self.assertEqual([row[0] for row in rows], list(range(1, 24)))
        self.assertEqual([row[1] for row in rows], list(expected))
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='workflow_runs' "
                "AND column_name='dependency_ready'"
            )
            self.assertIsNotNone(cursor.fetchone())
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT tgname FROM pg_catalog.pg_trigger "
                "WHERE NOT tgisinternal AND tgname IN "
                "('workflow_goal_ownership_immutable', 'workflow_goal_events_append_only')"
            )
            trigger_names = {row[0] for row in cursor.fetchall()}
        self.assertEqual(
            trigger_names,
            {"workflow_goal_ownership_immutable", "workflow_goal_events_append_only"},
        )
        with self.connection.cursor() as cursor:
            for table in ("conversations", "conversation_messages", "conversation_generation_leases", "conversation_generation_runs", "workflow_goals", "workflow_goal_tasks", "workflow_goal_dependencies", "workflow_goal_events"):
                cursor.execute("SELECT to_regclass(%s)", (f"public.{table}",))
                self.assertIsNotNone(cursor.fetchone()[0], table)

    def test_checksum_drift_fails_closed_without_applying_pending_work(self) -> None:
        postgres_migrations.migrate(self.connection, apply=True)
        with self.connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE {postgres_migrations.LEDGER_TABLE} SET checksum = %s WHERE version = 1",
                ("0" * 64,),
            )
        with self.assertRaisesRegex(postgres_migrations.MigrationError, "history mismatch"):
            postgres_migrations.migrate(self.connection, apply=False)


if __name__ == "__main__":
    unittest.main()
