"""Disposable PostgreSQL LOGIN-role tests for the authorization audit writer."""
import os
import unittest
import uuid
from pathlib import Path

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
class PostgresAuthorizationAuditWriterRoleIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin_url = POSTGRES_URL
        cls.role = "yuki_auth_writer_" + uuid.uuid4().hex[:12]
        cls.password = uuid.uuid4().hex
        migration = Path(__file__).resolve().parents[1] / "migrations/security/0006_authorization_audit.sql"
        with psycopg.connect(cls.admin_url) as conn:
            conn.execute(migration.read_text(encoding="utf-8"), prepare=False)
            conn.execute("CREATE TABLE IF NOT EXISTS yuki_schema_migrations (version TEXT PRIMARY KEY)")
            conn.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
            conn.execute("TRUNCATE authorization_audit_events")
            conn.execute("UPDATE authorization_audit_state SET last_event_id=0, head_hash=repeat('0',64) WHERE singleton=1")
            conn.execute("REVOKE ALL ON authorization_audit_events, authorization_audit_state, yuki_schema_migrations FROM PUBLIC")
            conn.execute(sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT"
            ).format(sql.Identifier(cls.role), sql.Literal(cls.password)))
            conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(cls.role)))
            event_columns = [
                "event_id", "occurred_at", "principal_hash", "tenant_hash", "agent_hash",
                "action", "resource_hash", "task_hash", "delegation_hash", "approval_hash",
                "risk", "allowed", "decision_hash", "policy_revision_hash", "reason_code",
                "previous_hash", "event_hash",
            ]
            conn.execute(sql.SQL("GRANT SELECT ({}) ON authorization_audit_events TO {}").format(
                sql.SQL(",").join(map(sql.Identifier, event_columns)), sql.Identifier(cls.role)))
            conn.execute(sql.SQL("GRANT INSERT ({}) ON authorization_audit_events TO {}").format(
                sql.SQL(",").join(map(sql.Identifier, event_columns)), sql.Identifier(cls.role)))
            conn.execute(sql.SQL(
                "GRANT SELECT (singleton,last_event_id,head_hash) ON authorization_audit_state TO {}"
            ).format(sql.Identifier(cls.role)))
            conn.execute(sql.SQL(
                "GRANT UPDATE (last_event_id,head_hash) ON authorization_audit_state TO {}"
            ).format(sql.Identifier(cls.role)))

    @classmethod
    def tearDownClass(cls):
        if not _ENABLED:
            return
        with psycopg.connect(cls.admin_url) as conn:
            conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(cls.role)))
            conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(cls.role)))

    @classmethod
    def writer_url(cls):
        params = conninfo_to_dict(cls.admin_url)
        params.update(user=cls.role, password=cls.password)
        return make_conninfo(**params)

    def setUp(self):
        with psycopg.connect(self.admin_url) as conn:
            conn.execute("TRUNCATE authorization_audit_events")
            conn.execute("UPDATE authorization_audit_state SET last_event_id=0, head_hash=repeat('0',64) WHERE singleton=1")
        from core.postgres_authorization_audit import PostgresAuthorizationAuditLog
        self.writer = PostgresAuthorizationAuditLog(self.writer_url(), min_pool_size=1, max_pool_size=1)

    def tearDown(self):
        self.writer.close()

    @staticmethod
    def event(index=1):
        from core.authorization import AuthorizationDecision, AuthorizationRequest, Principal
        request = AuthorizationRequest(
            Principal("worker-" + str(index), "workload", tenant_id="tenant-1", agent_id="agent-1"),
            "task:read", "resource:" + str(index), risk="high",
            task_id="task-" + str(index), expected_policy_revision="revision-1",
        )
        decision = AuthorizationDecision(True, "decision-" + str(index), "revision-1", "allowed")
        return request, decision

    def test_writer_readiness_append_checkpoint_and_chain_verification(self):
        self.assertTrue(self.writer.check_ready())
        request, decision = self.event()
        self.assertEqual(self.writer(request, decision), 1)
        self.assertTrue(self.writer.verify_chain())
        self.assertEqual(self.writer.checkpoint().event_id, 1)
        self.assertEqual(self.writer.event_page(limit=5)[0]["reason_code"], "allowed")

    def assert_denied(self, statement):
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with psycopg.connect(self.writer_url()) as conn:
                conn.execute(statement)

    def test_writer_cannot_mutate_existing_events_or_access_other_domains(self):
        request, decision = self.event()
        self.writer(request, decision)
        for statement in (
            "UPDATE authorization_audit_events SET reason_code='tampered' WHERE event_id=1",
            "DELETE FROM authorization_audit_events",
            "TRUNCATE authorization_audit_events",
            "UPDATE authorization_audit_state SET singleton=2",
            "SELECT * FROM yuki_schema_migrations",
            "SELECT * FROM security_work_leases",
            "CREATE TABLE public.authorization_writer_must_not_create (id integer)",
            "ALTER TABLE authorization_audit_events ADD COLUMN unsafe text",
        ):
            self.assert_denied(statement)

    def test_database_reports_exact_column_privileges_and_no_table_grants(self):
        event_columns = {
            "event_id", "occurred_at", "principal_hash", "tenant_hash", "agent_hash",
            "action", "resource_hash", "task_hash", "delegation_hash", "approval_hash",
            "risk", "allowed", "decision_hash", "policy_revision_hash", "reason_code",
            "previous_hash", "event_hash",
        }
        state_columns = {"singleton", "last_event_id", "head_hash"}
        with psycopg.connect(self.admin_url) as conn:
            for table, expected in (("authorization_audit_events", event_columns),
                                    ("authorization_audit_state", state_columns)):
                for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
                    self.assertFalse(conn.execute(
                        "SELECT has_table_privilege(%s,%s,%s)",
                        (self.role, "public." + table, privilege),
                    ).fetchone()[0], (table, privilege))
                for (column,) in conn.execute(
                    "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s",
                    (table,),
                ).fetchall():
                    for privilege in ("SELECT", "INSERT", "UPDATE"):
                        actual = conn.execute(
                            "SELECT has_column_privilege(%s,%s,%s,%s)",
                            (self.role, "public." + table, column, privilege),
                        ).fetchone()[0]
                        allowed = column in expected and (
                            privilege == "SELECT"
                            or (table == "authorization_audit_events" and privilege == "INSERT")
                            or (table == "authorization_audit_state" and privilege == "UPDATE" and column in {"last_event_id", "head_hash"})
                        )
                        self.assertEqual(actual, allowed, (table, column, privilege))


if __name__ == "__main__":
    unittest.main()
