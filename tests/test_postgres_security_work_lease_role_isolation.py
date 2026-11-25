"""Disposable PostgreSQL least-privilege tests for distributed security work leases."""
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
class PostgresWorkLeaseRoleIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        cls.admin_url = POSTGRES_URL
        cls.role = "yuki_work_lease_" + uuid.uuid4().hex[:12]
        cls.password = uuid.uuid4().hex
        with psycopg.connect(cls.admin_url) as conn:
            outbox_migration = root / "migrations/security/0003_security_alert_outbox.sql"
            conn.execute(outbox_migration.read_text(encoding="utf-8"), prepare=False)
            migration = root / "migrations/security/0001_security_work_leases.sql"
            conn.execute(migration.read_text(encoding="utf-8"), prepare=False)
            conn.execute("CREATE TABLE IF NOT EXISTS yuki_schema_migrations (version TEXT PRIMARY KEY)")
            conn.execute("TRUNCATE security_work_leases")
            conn.execute("REVOKE ALL ON security_work_leases, yuki_schema_migrations FROM PUBLIC")
            conn.execute(sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT"
            ).format(sql.Identifier(cls.role), sql.Literal(cls.password)))
            conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(cls.role)))
            conn.execute(sql.SQL(
                "GRANT SELECT (queue_name,resource_key,lease_owner,lease_token,lease_epoch,lease_expires_at) "
                "ON security_work_leases TO {}"
            ).format(sql.Identifier(cls.role)))
            conn.execute(sql.SQL(
                "GRANT INSERT (queue_name,resource_key,lease_owner,lease_token,lease_epoch,lease_expires_at,created_at,updated_at) "
                "ON security_work_leases TO {}"
            ).format(sql.Identifier(cls.role)))
            conn.execute(sql.SQL(
                "GRANT UPDATE (lease_owner,lease_token,lease_epoch,lease_expires_at,updated_at) "
                "ON security_work_leases TO {}"
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

    def test_runtime_claim_renew_and_release_work_with_restricted_role(self):
        from core.postgres_security_lease_store import PostgresSecurityLeaseStore
        store = PostgresSecurityLeaseStore(self.role_url(), min_pool_size=1, max_pool_size=1)
        try:
            self.assertTrue(store.check_ready())
            lease = store.claim("security-events", "incident/42", "worker-a", lease_seconds=60)
            self.assertIsNotNone(lease)
            self.assertIsNone(store.claim("security-events", "incident/42", "worker-b", lease_seconds=60))
            renewed = store.renew(lease, lease_seconds=90)
            self.assertIsNotNone(renewed)
            self.assertEqual(renewed.lease_epoch, lease.lease_epoch)
            self.assertTrue(store.release(renewed))
            replacement = store.claim("security-events", "incident/42", "worker-b", lease_seconds=60)
            self.assertIsNotNone(replacement)
            self.assertGreater(replacement.lease_epoch, lease.lease_epoch)
            self.assertFalse(store.release(lease), "stale fencing token must not release replacement lease")
            self.assertTrue(store.release(replacement))
        finally:
            store.close()

    def test_lease_role_cannot_delete_truncate_or_change_identity(self):
        for statement in (
            "DELETE FROM security_work_leases",
            "TRUNCATE security_work_leases",
            "UPDATE security_work_leases SET queue_name='attacker'",
            "UPDATE security_work_leases SET resource_key='attacker'",
            "SELECT created_at FROM security_work_leases",
            "CREATE TABLE public.lease_role_must_not_create (id integer)",
            "ALTER TABLE security_work_leases ADD COLUMN unsafe text",
            "SELECT * FROM yuki_schema_migrations",
            "SELECT * FROM security_alert_outbox",
        ):
            self.assert_denied(statement)

    def test_role_has_only_exact_column_privileges(self):
        expected_select = {
            "queue_name", "resource_key", "lease_owner", "lease_token",
            "lease_epoch", "lease_expires_at",
        }
        expected_insert = {
            "queue_name", "resource_key", "lease_owner", "lease_token",
            "lease_epoch", "lease_expires_at", "created_at", "updated_at",
        }
        expected_update = {
            "lease_owner", "lease_token", "lease_epoch", "lease_expires_at", "updated_at",
        }
        with psycopg.connect(self.admin_url) as conn:
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                self.assertFalse(conn.execute(
                    "SELECT has_table_privilege(%s,'public.security_work_leases',%s)",
                    (self.role, privilege),
                ).fetchone()[0], ("unexpected table grant", privilege))
            columns = conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='security_work_leases'"
            ).fetchall()
            for (column,) in columns:
                for privilege, expected in (
                    ("SELECT", column in expected_select),
                    ("INSERT", column in expected_insert),
                    ("UPDATE", column in expected_update),
                ):
                    actual = conn.execute(
                        "SELECT has_column_privilege(%s,'public.security_work_leases',%s,%s)",
                        (self.role, column, privilege),
                    ).fetchone()[0]
                    self.assertEqual(actual, expected, (column, privilege))
            for relation in ("yuki_schema_migrations", "security_alert_outbox"):
                for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                    actual = conn.execute(
                        "SELECT has_table_privilege(%s,%s,%s)",
                        (self.role, "public." + relation, privilege),
                    ).fetchone()[0]
                    self.assertFalse(actual, (relation, privilege))


if __name__ == "__main__":
    unittest.main()
