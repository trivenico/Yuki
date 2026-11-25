"""Disposable PostgreSQL tests for workload-key registry role isolation."""
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
class PostgresWorkloadKeyRegistryRoleIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        cls.admin_url = POSTGRES_URL
        cls.role = "yuki_key_registry_" + uuid.uuid4().hex[:12]
        cls.password = uuid.uuid4().hex
        with psycopg.connect(cls.admin_url) as conn:
            migration = root / "migrations/security/0004_workload_key_registry.sql"
            conn.execute(migration.read_text(encoding="utf-8"), prepare=False)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS yuki_schema_migrations (version TEXT PRIMARY KEY)"
            )
            conn.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
            conn.execute(
                "REVOKE ALL ON workload_keys, workload_key_audit, yuki_schema_migrations FROM PUBLIC"
            )
            conn.execute(sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT"
            ).format(sql.Identifier(cls.role), sql.Literal(cls.password)))
            conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(cls.role)))
            conn.execute(sql.SQL(
                "GRANT SELECT (issuer,key_id,algorithm,public_key,state) ON workload_keys TO {}"
            ).format(sql.Identifier(cls.role)))
            conn.execute(sql.SQL(
                "GRANT INSERT (issuer,key_id,algorithm,public_key,state) ON workload_keys TO {}"
            ).format(sql.Identifier(cls.role)))
            conn.execute(sql.SQL(
                "GRANT UPDATE (state,updated_at) ON workload_keys TO {}"
            ).format(sql.Identifier(cls.role)))
            conn.execute(sql.SQL(
                "GRANT SELECT (event_id,administrator,issuer,key_id,action,previous_state,new_state,occurred_at) "
                "ON workload_key_audit TO {}"
            ).format(sql.Identifier(cls.role)))
            conn.execute(sql.SQL(
                "GRANT INSERT (administrator,issuer,key_id,action,previous_state,new_state) "
                "ON workload_key_audit TO {}"
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

    def setUp(self):
        from core.postgres_workload_key_registry import PostgresWorkloadKeyRegistry
        self.admin = "role-isolation-admin"
        self.issuer = "role-isolation-" + uuid.uuid4().hex
        self.registry = PostgresWorkloadKeyRegistry(
            self.role_url(), authorize_admin=lambda value: value == self.admin,
            min_pool_size=1, max_pool_size=1,
        )

    def tearDown(self):
        self.registry.close()

    def key(self, key_id, byte):
        from core.workload_identity import WorkloadSigningKey
        return WorkloadSigningKey(self.issuer, key_id, "EdDSA", byte * 32)

    def assert_denied(self, statement):
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with psycopg.connect(self.role_url()) as conn:
                conn.execute(statement)

    def test_registry_lifecycle_audit_and_readiness_use_restricted_role(self):
        self.assertTrue(self.registry.check_ready())
        staged = self.key("staged", b"s")
        replacement = self.key("replacement", b"r")
        self.registry.register(self.admin, staged)
        self.assertIsNone(self.registry.resolve(self.issuer, staged.key_id, "EdDSA"))
        self.registry.activate(self.admin, self.issuer, staged.key_id)
        self.assertEqual(self.registry.resolve(self.issuer, staged.key_id, "EdDSA"), staged)
        self.registry.register(self.admin, replacement)
        self.registry.activate(self.admin, self.issuer, replacement.key_id)
        self.registry.retire(self.admin, self.issuer, staged.key_id)
        self.assertIsNone(self.registry.resolve(self.issuer, staged.key_id, "EdDSA"))
        self.assertEqual(self.registry.resolve(self.issuer, replacement.key_id, "EdDSA"), replacement)
        events = self.registry.audit_events(self.admin, limit=20)
        self.assertEqual(
            [row["action"] for row in events if row["issuer"] == self.issuer],
            ["register", "active", "register", "active", "retired"],
        )

    def test_audit_is_append_only_and_unrelated_domains_are_denied(self):
        self.registry.register(self.admin, self.key("immutable", b"i"))
        for statement in (
            "UPDATE workload_key_audit SET action='revoked'",
            "DELETE FROM workload_key_audit",
            "TRUNCATE workload_key_audit",
            "DELETE FROM workload_keys",
            "TRUNCATE workload_keys",
            "CREATE TABLE public.key_role_must_not_create (id integer)",
            "ALTER TABLE workload_keys ADD COLUMN unsafe text",
            "SELECT * FROM security_monitor_signals",
            "SELECT * FROM security_alert_outbox",
            "SELECT * FROM security_work_leases",
            "SELECT * FROM yuki_schema_migrations",
            "SELECT * FROM authorization_audit_events",
        ):
            self.assert_denied(statement)

    def test_exact_column_privileges_no_table_grants_and_no_sequence_access(self):
        expected = {
            "workload_keys": {
                "SELECT": {"issuer", "key_id", "algorithm", "public_key", "state"},
                "INSERT": {"issuer", "key_id", "algorithm", "public_key", "state"},
                "UPDATE": {"state", "updated_at"},
            },
            "workload_key_audit": {
                "SELECT": {"event_id", "administrator", "issuer", "key_id", "action",
                           "previous_state", "new_state", "occurred_at"},
                "INSERT": {"administrator", "issuer", "key_id", "action",
                           "previous_state", "new_state"},
                "UPDATE": set(),
            },
        }
        with psycopg.connect(self.admin_url) as conn:
            for relation, privileges in expected.items():
                for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                    self.assertFalse(conn.execute(
                        "SELECT has_table_privilege(%s,%s,%s)",
                        (self.role, "public." + relation, privilege),
                    ).fetchone()[0], (relation, privilege, "unexpected table grant"))
                columns = conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name=%s", (relation,),
                ).fetchall()
                for (column,) in columns:
                    for privilege in ("SELECT", "INSERT", "UPDATE"):
                        actual = conn.execute(
                            "SELECT has_column_privilege(%s,%s,%s,%s)",
                            (self.role, "public." + relation, column, privilege),
                        ).fetchone()[0]
                        self.assertEqual(
                            actual, column in privileges[privilege],
                            (relation, column, privilege),
                        )
            sequence = conn.execute(
                "SELECT pg_get_serial_sequence('public.workload_key_audit','event_id')"
            ).fetchone()[0]
            self.assertIsNotNone(sequence)
            for privilege in ("USAGE", "SELECT", "UPDATE"):
                self.assertFalse(conn.execute(
                    "SELECT has_sequence_privilege(%s,%s,%s)",
                    (self.role, sequence, privilege),
                ).fetchone()[0], ("unexpected identity sequence grant", privilege))


if __name__ == "__main__":
    unittest.main()
