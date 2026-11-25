"""Disposable PostgreSQL tests for audit-reader role isolation.

The test database must be disposable and the configured URL must have CREATEROLE
and schema ownership privileges. The test creates uniquely named, temporary
LOGIN roles and removes them in tearDownClass.
"""
import os
import unittest
import uuid
from urllib.parse import quote

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
class PostgresAuditReaderRoleIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        cls.admin_url = POSTGRES_URL
        cls.auth_role = "yuki_auth_reader_" + uuid.uuid4().hex[:12]
        cls.key_role = "yuki_key_reader_" + uuid.uuid4().hex[:12]
        cls.auth_password = uuid.uuid4().hex
        cls.key_password = uuid.uuid4().hex
        with psycopg.connect(cls.admin_url) as conn:
            for migration in ("0004_workload_key_registry.sql", "0006_authorization_audit.sql"):
                path = root / "migrations/security" / migration
                conn.execute(path.read_text(encoding="utf-8"), prepare=False)
            # A sentinel migration ledger lets the test prove readers cannot
            # inspect deployment-owned migration metadata.
            conn.execute("CREATE TABLE IF NOT EXISTS yuki_schema_migrations (version TEXT PRIMARY KEY)")
            conn.execute("REVOKE ALL ON authorization_audit_events, authorization_audit_state, workload_key_audit, workload_keys, yuki_schema_migrations FROM PUBLIC")
            for role, password in ((cls.auth_role, cls.auth_password), (cls.key_role, cls.key_password)):
                conn.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT").format(
                    sql.Identifier(role), sql.Literal(password)
                ))
            conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}, {}").format(
                sql.Identifier(cls.auth_role), sql.Identifier(cls.key_role)
            ))
            conn.execute(sql.SQL("GRANT SELECT (event_id, occurred_at, principal_hash, reason_code, allowed) ON authorization_audit_events TO {}").format(sql.Identifier(cls.auth_role)))
            conn.execute(sql.SQL("GRANT SELECT (singleton) ON authorization_audit_state TO {}").format(sql.Identifier(cls.auth_role)))
            conn.execute(sql.SQL("GRANT SELECT (event_id, administrator, issuer, key_id, action, previous_state, new_state, occurred_at) ON workload_key_audit TO {}").format(sql.Identifier(cls.key_role)))
            # No grants are made on workload_keys or the migration ledger.
            conn.execute("INSERT INTO workload_key_audit (administrator,issuer,key_id,action,previous_state,new_state) VALUES ('test-admin','role-test','key-1','register',NULL,'staged')")
            conn.execute("INSERT INTO authorization_audit_events (event_id,occurred_at,principal_hash,action,resource_hash,risk,allowed,decision_hash,policy_revision_hash,reason_code,previous_hash,event_hash) VALUES (1,'2026-01-01T00:00:00+00:00','p-hash','read','r-hash','low',1,'d-hash','policy-hash','allowed',repeat('0',64),repeat('1',64))")
            conn.execute("UPDATE authorization_audit_state SET last_event_id=1, head_hash=repeat('1',64) WHERE singleton=1")

    @classmethod
    def tearDownClass(cls):
        if not _ENABLED:
            return
        with psycopg.connect(cls.admin_url) as conn:
            conn.execute(sql.SQL("DROP OWNED BY {}, {}").format(sql.Identifier(cls.auth_role), sql.Identifier(cls.key_role)))
            conn.execute(sql.SQL("DROP ROLE {}, {}").format(sql.Identifier(cls.auth_role), sql.Identifier(cls.key_role)))

    @classmethod
    def reader_url(cls, role, password):
        params = conninfo_to_dict(cls.admin_url)
        params.update(user=role, password=password)
        return make_conninfo(**params)

    def assert_denied(self, url, statement):
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with psycopg.connect(url) as conn:
                conn.execute(statement)

    def test_authorization_reader_has_only_required_select_projection(self):
        from core.postgres_security_audit_readers import PostgresAuthorizationAuditReader
        url = self.reader_url(self.auth_role, self.auth_password)
        reader = PostgresAuthorizationAuditReader(url)
        try:
            self.assertTrue(reader.check_ready())
            rows = reader.event_page(limit=5)
            self.assertEqual(len(rows), 1)
            self.assertEqual(set(rows[0]), {"event_id", "occurred_at", "principal_hash", "reason_code", "allowed"})
        finally:
            reader.close()
        for statement in (
            "INSERT INTO authorization_audit_events (event_id,occurred_at,principal_hash,action,resource_hash,risk,allowed,decision_hash,policy_revision_hash,reason_code,previous_hash,event_hash) VALUES (2,'x','p','a','r','low',1,'d','p','ok',repeat('0',64),repeat('2',64))",
            "UPDATE authorization_audit_state SET last_event_id=2",
            "DELETE FROM authorization_audit_events",
            "TRUNCATE authorization_audit_events",
            "CREATE TABLE public.reader_must_not_create (id integer)",
            "SELECT * FROM yuki_schema_migrations",
            "SELECT * FROM workload_keys",
        ):
            self.assert_denied(url, statement)

    def test_workload_key_reader_has_only_audit_select_and_no_writer_access(self):
        from core.postgres_security_audit_readers import PostgresWorkloadKeyAuditReader
        url = self.reader_url(self.key_role, self.key_password)
        reader = PostgresWorkloadKeyAuditReader(url)
        try:
            self.assertTrue(reader.check_ready())
            rows = reader.audit_events(limit=5)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["key_id"], "key-1")
        finally:
            reader.close()
        for statement in (
            "INSERT INTO workload_key_audit (administrator,issuer,key_id,action,new_state) VALUES ('x','i','k','register','staged')",
            "UPDATE workload_key_audit SET action='revoked'",
            "DELETE FROM workload_key_audit",
            "TRUNCATE workload_key_audit",
            "SELECT * FROM workload_keys",
            "SELECT * FROM yuki_schema_migrations",
            "CREATE TABLE public.reader_must_not_create (id integer)",
        ):
            self.assert_denied(url, statement)

    def test_database_reports_no_unintended_table_privileges(self):
        checks = (
            (self.auth_role, "authorization_audit_events", "SELECT", True),
            (self.auth_role, "authorization_audit_state", "SELECT", True),
            (self.auth_role, "workload_key_audit", "SELECT", False),
            (self.auth_role, "workload_keys", "SELECT", False),
            (self.auth_role, "yuki_schema_migrations", "SELECT", False),
            (self.key_role, "workload_key_audit", "SELECT", True),
            (self.key_role, "workload_keys", "SELECT", False),
            (self.key_role, "authorization_audit_events", "SELECT", False),
            (self.key_role, "yuki_schema_migrations", "SELECT", False),
        )
        with psycopg.connect(self.admin_url) as conn:
            for role, relation, privilege, expected in checks:
                actual = conn.execute(
                    "SELECT has_table_privilege(%s,%s,%s)",
                    (role, "public." + relation, privilege),
                ).fetchone()[0]
                self.assertIs(actual, expected, (role, relation, privilege))


if __name__ == "__main__":
    unittest.main()
