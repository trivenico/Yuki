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


class MigrationCredentialTests(unittest.TestCase):
    def test_systemd_credential_is_loaded_without_environment_dsn(self) -> None:
        with patch.dict("os.environ", {"CREDENTIALS_DIRECTORY": "/run/credentials/yuki-migrate"}, clear=True):
            with patch("core.postgres_migrations.Path.read_text", return_value="postgresql://migrator:secret@db/yuki"):
                psycopg_module = MagicMock()
                with patch.dict("sys.modules", {"psycopg": psycopg_module}):
                    with patch("core.postgres_migrations.migrate", return_value=()) as migrate:
                        with patch("sys.argv", ["yuki-db-migrate", "--plan"]):
                            self.assertEqual(migrations.main(), 0)
        psycopg_module.connect.assert_called_once_with(
            "postgresql://migrator:secret@db/yuki", autocommit=True,
        )
        self.assertEqual(migrate.call_count, 1)

    def test_ambiguous_systemd_and_environment_credentials_are_rejected(self) -> None:
        with patch.dict(
            "os.environ",
            {"CREDENTIALS_DIRECTORY": "/run/credentials/yuki-migrate", "YUKI_DATABASE_URL": "postgresql://db/yuki"},
            clear=True,
        ):
            with patch("sys.argv", ["yuki-db-migrate", "--plan"]):
                with self.assertRaises(SystemExit):
                    migrations.main()

    def test_missing_systemd_credential_fails_closed(self) -> None:
        with patch.dict("os.environ", {"CREDENTIALS_DIRECTORY": "/run/credentials/yuki-migrate"}, clear=True):
            with patch("core.postgres_migrations.Path.read_text", side_effect=OSError("missing")):
                with patch("sys.argv", ["yuki-db-migrate", "--plan"]):
                    with self.assertRaises(SystemExit):
                        migrations.main()


    def test_connection_exception_does_not_disclose_database_url(self) -> None:
        import io
        from contextlib import redirect_stderr

        secret_dsn = "postgresql://migrator:top-secret-password@db.internal/yuki"
        psycopg_module = MagicMock()
        psycopg_module.connect.side_effect = RuntimeError(f"connection failed for {secret_dsn}")
        stderr = io.StringIO()
        with patch.dict("os.environ", {"YUKI_DATABASE_URL": secret_dsn}, clear=True):
            with patch.dict("sys.modules", {"psycopg": psycopg_module}):
                with patch("sys.argv", ["yuki-db-migrate", "--plan"]):
                    with redirect_stderr(stderr):
                        self.assertEqual(migrations.main(), 1)
        self.assertNotIn(secret_dsn, stderr.getvalue())
        self.assertNotIn("top-secret-password", stderr.getvalue())
        self.assertIn("RuntimeError", stderr.getvalue())
        self.assertIn("details redacted", stderr.getvalue())



class MigrationSessionCleanupTests(unittest.TestCase):
    def test_reset_role_failure_closes_connection_before_propagating(self) -> None:
        cursor = MagicMock()
        connection = MagicMock()
        reset_error = RuntimeError("RESET ROLE failed")
        cursor.execute.side_effect = reset_error

        with self.assertRaisesRegex(RuntimeError, "RESET ROLE failed"):
            migrations._reset_role_or_close_connection(cursor, connection)

        connection.close.assert_called_once_with()

    def test_reset_role_success_keeps_connection_open(self) -> None:
        cursor = MagicMock()
        connection = MagicMock()

        migrations._reset_role_or_close_connection(cursor, connection)

        cursor.execute.assert_called_once_with("RESET ROLE")
        connection.close.assert_not_called()

    def test_reset_role_failure_still_propagates_if_connection_close_fails(self) -> None:
        cursor = MagicMock()
        connection = MagicMock()
        cursor.execute.side_effect = RuntimeError("RESET ROLE failed")
        connection.close.side_effect = OSError("socket close failed")

        with self.assertRaisesRegex(RuntimeError, "RESET ROLE failed"):
            migrations._reset_role_or_close_connection(cursor, connection)

        connection.close.assert_called_once_with()


class MigrationRoleCleanupIntegrationUnitTests(unittest.TestCase):
    def _connection_for_plan(self, *, fail_unlock=False, fail_reset=False):
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.fetchone.side_effect = [
            (True, False, False, False, False, False, False),  # NOINHERIT migration executor LOGIN
            (False, False, False, False, False, False, False),  # NOLOGIN/NOINHERIT owner role
            (None,),  # safe initialization ledger check
            (None,),  # ledger existence check
        ]
        cursor.fetchall.side_effect = [
            [("yuki_schema_owner",)],  # executor's only role membership
            [],  # owner role has no parent memberships
            [],  # no other LOGIN/member can assume the owner role
            [],  # empty public schema
        ]
        executed = []

        def execute(statement, *args, **kwargs):
            rendered = str(statement)
            executed.append(rendered)
            if fail_unlock and "pg_advisory_unlock" in rendered:
                raise RuntimeError("advisory unlock failed")
            if fail_reset and rendered == "RESET ROLE":
                raise RuntimeError("role reset failed")

        cursor.execute.side_effect = execute
        return connection, cursor, executed

    def test_role_reset_is_attempted_even_when_advisory_unlock_fails(self):
        connection, cursor, executed = self._connection_for_plan(fail_unlock=True)
        psycopg_module = MagicMock()
        with patch.dict("sys.modules", {"psycopg": psycopg_module}):
            with patch.object(migrations, "load_migrations", return_value=()):
                with self.assertRaisesRegex(RuntimeError, "advisory unlock failed"):
                    migrations.migrate(connection, apply=False, owner_role="yuki_schema_owner")

        self.assertIn("RESET ROLE", executed)
        cursor.close.assert_called_once_with()
        connection.close.assert_not_called()

    def test_failed_role_reset_closes_the_migration_connection(self):
        connection, cursor, executed = self._connection_for_plan(fail_reset=True)
        psycopg_module = MagicMock()
        with patch.dict("sys.modules", {"psycopg": psycopg_module}):
            with patch.object(migrations, "load_migrations", return_value=()):
                with self.assertRaisesRegex(RuntimeError, "role reset failed"):
                    migrations.migrate(connection, apply=False, owner_role="yuki_schema_owner")

        self.assertTrue(any("pg_advisory_unlock" in statement for statement in executed))
        connection.close.assert_called_once_with()
        cursor.close.assert_called_once_with()


class MigrationExecutionTests(unittest.TestCase):
    def test_executor_must_be_a_dedicated_non_escalating_noinherit_login(self) -> None:
        cursor = MagicMock()
        cursor.fetchone.return_value = (True, False, False, False, False, False, False)
        cursor.fetchall.return_value = [("yuki_schema_owner",)]
        migrations._validate_executor_role(cursor, "yuki_schema_owner")
        self.assertEqual(cursor.execute.call_count, 2)

    def test_executor_rejects_non_login_inheriting_and_escalating_roles(self) -> None:
        for attributes in (
            (False, False, False, False, False, False, False),
            (True, True, False, False, False, False, False),
            (True, False, True, False, False, False, False),
            (True, False, False, True, False, False, False),
            (True, False, False, False, True, False, False),
            (True, False, False, False, False, True, False),
            (True, False, False, False, False, False, True),
        ):
            with self.subTest(attributes=attributes):
                cursor = MagicMock()
                cursor.fetchone.return_value = attributes
                with self.assertRaises(migrations.MigrationError):
                    migrations._validate_executor_role(cursor, "yuki_schema_owner")

    def test_executor_rejects_additional_role_memberships(self) -> None:
        cursor = MagicMock()
        cursor.fetchone.return_value = (True, False, False, False, False, False, False)
        cursor.fetchall.return_value = [("yuki_schema_owner",), ("other_privileged_role",)]
        with self.assertRaisesRegex(migrations.MigrationError, "only in the configured"):
            migrations._validate_executor_role(cursor, "yuki_schema_owner")

    def test_executor_rejects_missing_owner_role_membership(self) -> None:
        cursor = MagicMock()
        cursor.fetchone.return_value = (True, False, False, False, False, False, False)
        cursor.fetchall.return_value = []
        with self.assertRaisesRegex(migrations.MigrationError, "only in the configured"):
            migrations._validate_executor_role(cursor, "yuki_schema_owner")

    def test_missing_executor_identity_is_rejected(self) -> None:
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        with self.assertRaisesRegex(migrations.MigrationError, "LOGIN"):
            migrations._validate_executor_role(cursor, "yuki_schema_owner")

    def test_owner_role_must_exist(self) -> None:
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        with self.assertRaisesRegex(migrations.MigrationError, "does not exist"):
            migrations._validate_owner_role(cursor, "yuki_schema_owner")

    def test_owner_role_must_be_nologin_noinherit_and_non_escalating(self) -> None:
        for attributes in (
            (True, False, False, False, False, False, False),
            (False, True, False, False, False, False, False),
            (False, False, True, False, False, False, False),
            (False, False, False, True, False, False, False),
            (False, False, False, False, True, False, False),
            (False, False, False, False, False, True, False),
            (False, False, False, False, False, False, True),
        ):
            with self.subTest(attributes=attributes):
                cursor = MagicMock()
                cursor.fetchone.return_value = attributes
                with self.assertRaisesRegex(migrations.MigrationError, "NOLOGIN"):
                    migrations._validate_owner_role(cursor, "yuki_schema_owner")

    def test_owner_role_rejects_parent_memberships(self) -> None:
        cursor = MagicMock()
        cursor.fetchone.return_value = (False, False, False, False, False, False, False)
        cursor.fetchall.return_value = [("other_privileged_role",)]
        with self.assertRaisesRegex(migrations.MigrationError, "must not be a member"):
            migrations._validate_owner_role(cursor, "yuki_schema_owner")

    def test_owner_role_rejects_other_members(self) -> None:
        cursor = MagicMock()
        cursor.fetchone.return_value = (False, False, False, False, False, False, False)
        cursor.fetchall.side_effect = [[], [("unexpected_member",)]]
        with self.assertRaisesRegex(migrations.MigrationError, "granted only to"):
            migrations._validate_owner_role(cursor, "yuki_schema_owner")

    def test_safe_owner_role_is_accepted(self) -> None:
        cursor = MagicMock()
        cursor.fetchone.return_value = (False, False, False, False, False, False, False)
        cursor.fetchall.return_value = []
        migrations._validate_owner_role(cursor, "yuki_schema_owner")
        self.assertEqual(cursor.execute.call_count, 2)

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
        tx_cursor.execute.side_effect = [None, None, RuntimeError("ddl failed")]
        first = migrations.Migration(1, "0001_test.sql", "SELECT 1", "a" * 64)
        second = migrations.Migration(2, "0002_test.sql", "SELECT 2", "b" * 64)
        with patch.object(migrations, "load_migrations", return_value=(first, second)):
            with self.assertRaisesRegex(RuntimeError, "ddl failed"):
                migrations.migrate(connection, apply=True)
        self.assertEqual(tx_cursor.execute.call_count, 3)
        self.assertEqual(
            tx_cursor.execute.call_args_list[1],
            call("SET LOCAL search_path = public, pg_temp"),
        )
        self.assertEqual(tx_cursor.execute.call_args_list[2], call("SELECT 1"))


if __name__ == "__main__":
    unittest.main()
