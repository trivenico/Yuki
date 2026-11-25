"""Disposable PostgreSQL tests for the ingestion cursor service role."""
import os
import unittest
import uuid

POSTGRES_URL = os.environ.get("YUKI_TEST_POSTGRES_URL")
try:
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
except ImportError:  # pragma: no cover
    psycopg = None
    sql = None
    conninfo_to_dict = None
    make_conninfo = None

_ENABLED = bool(POSTGRES_URL and psycopg)


@unittest.skipUnless(_ENABLED, "YUKI_TEST_POSTGRES_URL and psycopg are required")
class PostgresIngestionCursorRoleIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        cls.admin_url = POSTGRES_URL
        cls.role = "yuki_ingest_cursor_" + uuid.uuid4().hex[:12]
        cls.password = uuid.uuid4().hex
        with psycopg.connect(cls.admin_url) as conn:
            migration = root / "migrations/security/0002_security_ingestion_cursors.sql"
            conn.execute(migration.read_text(encoding="utf-8"), prepare=False)
            # The negative cross-table check below must target a real relation
            # even when this test is run alone rather than after audit-reader tests.
            workload_migration = root / "migrations/security/0004_workload_key_registry.sql"
            conn.execute(workload_migration.read_text(encoding="utf-8"), prepare=False)
            conn.execute("CREATE TABLE IF NOT EXISTS yuki_schema_migrations (version TEXT PRIMARY KEY)")
            conn.execute("TRUNCATE security_ingestion_cursors")
            conn.execute("REVOKE ALL ON security_ingestion_cursors, yuki_schema_migrations FROM PUBLIC")
            conn.execute(sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT"
            ).format(sql.Identifier(cls.role), sql.Literal(cls.password)))
            conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(cls.role)))
            # SQL-derived minimum: all columns are read by predicates, snapshots,
            # readiness and RETURNING; only source is inserted; mutable state only
            # is updated. The service cannot delete rows or alter the schema.
            conn.execute(sql.SQL(
                "GRANT SELECT (source,event_id,lease_owner,lease_token,lease_epoch,lease_until,last_success_at,last_error_code) "
                "ON security_ingestion_cursors TO {}"
            ).format(sql.Identifier(cls.role)))
            conn.execute(sql.SQL(
                "GRANT INSERT (source) ON security_ingestion_cursors TO {}"
            ).format(sql.Identifier(cls.role)))
            conn.execute(sql.SQL(
                "GRANT UPDATE (event_id,lease_owner,lease_token,lease_epoch,lease_until,last_success_at,last_error_code) "
                "ON security_ingestion_cursors TO {}"
            ).format(sql.Identifier(cls.role)))

    @classmethod
    def tearDownClass(cls):
        if not _ENABLED:
            return
        with psycopg.connect(cls.admin_url) as conn:
            conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(cls.role)))
            conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(cls.role)))

    @classmethod
    def role_url(cls):
        params = conninfo_to_dict(cls.admin_url)
        params.update(user=cls.role, password=cls.password)
        return make_conninfo(**params)

    def assert_denied(self, statement):
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with psycopg.connect(self.role_url()) as conn:
                conn.execute(statement)

    def test_runtime_cursor_contract_works_with_minimum_role(self):
        from core.postgres_security_event_ingestion import PostgresSecurityEventIngestionCursors
        store = PostgresSecurityEventIngestionCursors(
            self.role_url(), min_pool_size=1, max_pool_size=1,
        )
        try:
            self.assertTrue(store.check_ready())
            auth = store.claim("authorization_audit", "audit-worker-1")
            self.assertIsNotNone(auth)
            self.assertEqual(store.complete(auth, "audit-worker-1", 12), 12)
            key = store.claim("workload_key_audit", "key-worker-1")
            self.assertIsNotNone(key)
            store.fail(key, "key-worker-1", "source_unavailable")
            snapshot = store.snapshot()
            self.assertEqual(snapshot["authorization_audit"]["event_id"], 12)
            self.assertEqual(snapshot["workload_key_audit"]["event_id"], 0)
            self.assertEqual(snapshot["workload_key_audit"]["last_error_code"], "source_unavailable")
        finally:
            store.close()

    def test_cursor_role_cannot_delete_truncate_or_change_schema(self):
        for statement in (
            "DELETE FROM security_ingestion_cursors",
            "TRUNCATE security_ingestion_cursors",
            "UPDATE security_ingestion_cursors SET source='untrusted'",
            "INSERT INTO security_ingestion_cursors (source,event_id) VALUES ('authorization_audit',99)",
            "CREATE TABLE public.cursor_role_must_not_create (id integer)",
            "ALTER TABLE security_ingestion_cursors ADD COLUMN unsafe text",
            "SELECT * FROM yuki_schema_migrations",
            "SELECT * FROM workload_keys",
        ):
            self.assert_denied(statement)

    def test_role_has_only_column_scoped_cursor_privileges(self):
        expected_select = {
            "source", "event_id", "lease_owner", "lease_token",
            "lease_epoch", "lease_until", "last_success_at", "last_error_code",
        }
        expected_insert = {"source"}
        expected_update = {
            "event_id", "lease_owner", "lease_token", "lease_epoch",
            "lease_until", "last_success_at", "last_error_code",
        }
        with psycopg.connect(self.admin_url) as conn:
            self.assertFalse(conn.execute(
                "SELECT has_table_privilege(%s,'public.security_ingestion_cursors','SELECT')",
                (self.role,),
            ).fetchone()[0])
            self.assertFalse(conn.execute(
                "SELECT has_table_privilege(%s,'public.security_ingestion_cursors','INSERT')",
                (self.role,),
            ).fetchone()[0])
            self.assertFalse(conn.execute(
                "SELECT has_table_privilege(%s,'public.security_ingestion_cursors','UPDATE')",
                (self.role,),
            ).fetchone()[0])
            columns = conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='security_ingestion_cursors'"
            ).fetchall()
            for (column,) in columns:
                for privilege, expected in (
                    ("SELECT", column in expected_select),
                    ("INSERT", column in expected_insert),
                    ("UPDATE", column in expected_update),
                ):
                    actual = conn.execute(
                        "SELECT has_column_privilege(%s,'public.security_ingestion_cursors',%s,%s)",
                        (self.role, column, privilege),
                    ).fetchone()[0]
                    self.assertEqual(actual, expected, (column, privilege))
            for relation in ("yuki_schema_migrations", "workload_keys"):
                for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                    actual = conn.execute(
                        "SELECT has_table_privilege(%s,%s,%s)",
                        (self.role, "public." + relation, privilege),
                    ).fetchone()[0]
                    self.assertFalse(actual, (relation, privilege))


if __name__ == "__main__":
    unittest.main()
