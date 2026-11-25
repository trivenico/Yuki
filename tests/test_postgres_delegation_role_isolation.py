"""Disposable PostgreSQL least-privilege tests for workload delegation."""
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
class PostgresDelegationRoleIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        cls.admin_url = POSTGRES_URL
        cls.role = "yuki_delegation_" + uuid.uuid4().hex[:12]
        cls.password = uuid.uuid4().hex
        with psycopg.connect(cls.admin_url) as conn:
            for migration_name in ("0001_security_work_leases.sql", "0003_security_alert_outbox.sql", "0005_security_monitor.sql", "0007_workflow_runs.sql", "0008_workload_delegations.sql"):
                migration = root / "migrations/security" / migration_name
                conn.execute(migration.read_text(encoding="utf-8"), prepare=False)
            conn.execute("CREATE TABLE IF NOT EXISTS yuki_schema_migrations (version TEXT PRIMARY KEY)")
            conn.execute(
                "TRUNCATE workload_delegation_events, workload_delegations, task_events, workflow_runs"
            )
            conn.execute(
                "REVOKE ALL ON workload_delegations, workload_delegation_events, "
                "workflow_runs, task_events, yuki_schema_migrations, security_alert_outbox, "
                "security_monitor_incidents, security_work_leases FROM PUBLIC"
            )
            conn.execute("REVOKE ALL ON SEQUENCE workload_delegation_events_event_id_seq FROM PUBLIC")
            conn.execute(sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT"
            ).format(sql.Identifier(cls.role), sql.Literal(cls.password)))
            conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(cls.role)))
            conn.execute(sql.SQL(
                "GRANT SELECT (delegation_id,grantor_id,grantee_id,tenant_id,agent_id,task_id,"
                "lease_generation,scopes_json,issued_at,expires_at,parent_id,revoked_at), "
                "INSERT (delegation_id,grantor_id,grantee_id,tenant_id,agent_id,task_id,"
                "lease_generation,scopes_json,issued_at,expires_at,parent_id), "
                "UPDATE (revoked_at) ON workload_delegations TO {}"
            ).format(sql.Identifier(cls.role)))
            conn.execute(sql.SQL(
                "GRANT SELECT (event_id,event_type,delegation_id), "
                "INSERT (event_type,delegation_id,actor_id,task_id,scopes_json,created_at) "
                "ON workload_delegation_events TO {}"
            ).format(sql.Identifier(cls.role)))
            conn.execute(sql.SQL(
                "GRANT USAGE ON SEQUENCE workload_delegation_events_event_id_seq TO {}"
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
        from core.postgres_delegation_authority import PostgresDelegationAuthority
        self.authority = PostgresDelegationAuthority(self.role_url(), min_pool_size=1, max_pool_size=1)
        self.now = 1_900_000_000

    def tearDown(self):
        self.authority.close()

    def grant(self, **overrides):
        args = dict(
            grantor_id="human:owner", grantee_id="workload:agent",
            tenant_id="tenant-a", agent_id="agent-a", task_id="task-" + uuid.uuid4().hex,
            lease_generation="17", scopes={"task:progress", "task:complete"},
            expires_at=self.now + 300, now=self.now,
        )
        args.update(overrides)
        return self.authority.create_grant(**args)

    def assert_denied(self, statement):
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with psycopg.connect(self.role_url()) as conn:
                conn.execute(statement)

    def test_delegation_issue_verify_revoke_and_audit_lifecycle(self):
        task_id = "task-" + uuid.uuid4().hex
        parent = self.grant(task_id=task_id)
        child = self.grant(
            grantor_id="workload:agent", grantee_id="workload:child",
            task_id=task_id, scopes={"task:progress"}, expires_at=self.now + 200,
            parent_id=parent.delegation_id,
        )
        verified = self.authority.verify(
            child.delegation_id, grantee_id="workload:child",
            tenant_id="tenant-a", agent_id="agent-a", task_id=task_id,
            lease_generation="17", scopes={"task:progress"}, now=self.now + 1,
        )
        self.assertEqual(verified.delegation_id, child.delegation_id)
        self.assertTrue(self.authority.revoke(
            parent.delegation_id, revoked_by="human:owner", now=self.now + 2,
        ))
        with self.assertRaises(PermissionError):
            self.authority.verify(
                child.delegation_id, grantee_id="workload:child",
                tenant_id="tenant-a", agent_id="agent-a", task_id=task_id,
                lease_generation="17", scopes={"task:progress"}, now=self.now + 3,
            )
        with psycopg.connect(self.admin_url) as conn:
            events = conn.execute(
                "SELECT event_type,actor_id FROM workload_delegation_events "
                "WHERE delegation_id IN (%s,%s) ORDER BY event_id",
                (parent.delegation_id, child.delegation_id),
            ).fetchall()
        self.assertEqual(
            events,
            [("issued", "human:owner"), ("issued", "workload:agent"),
             ("verified", "workload:child"), ("revoked", "human:owner")],
        )

    def test_child_cannot_escalate_scope_or_expiry(self):
        task_id = "task-" + uuid.uuid4().hex
        parent = self.grant(task_id=task_id, scopes={"task:progress"}, expires_at=self.now + 120)
        with self.assertRaises(PermissionError):
            self.grant(
                grantor_id="workload:agent", grantee_id="workload:child",
                task_id=task_id, scopes={"task:complete"}, expires_at=self.now + 100,
                parent_id=parent.delegation_id,
            )
        with self.assertRaises(PermissionError):
            self.grant(
                grantor_id="workload:agent", grantee_id="workload:child",
                task_id=task_id, scopes={"task:progress"}, expires_at=self.now + 121,
                parent_id=parent.delegation_id,
            )

    def test_delegation_role_cannot_access_workflow_or_security_relations(self):
        for statement in (
            "SELECT * FROM workflow_runs",
            "SELECT * FROM task_events",
            "SELECT * FROM yuki_schema_migrations",
            "SELECT * FROM security_alert_outbox",
            "SELECT * FROM security_monitor_incidents",
            "SELECT * FROM security_work_leases",
            "UPDATE workload_delegation_events SET actor_id='tampered'",
            "DELETE FROM workload_delegation_events",
            "DELETE FROM workload_delegations",
            "TRUNCATE workload_delegations",
            "CREATE TABLE public.delegation_role_must_not_create (id integer)",
            "ALTER TABLE workload_delegations ADD COLUMN unsafe text",
        ):
            self.assert_denied(statement)

    def test_role_has_only_declared_column_and_sequence_privileges(self):
        expected = {
            "workload_delegations": {
                "SELECT": {"delegation_id", "grantor_id", "grantee_id", "tenant_id", "agent_id",
                           "task_id", "lease_generation", "scopes_json", "issued_at", "expires_at",
                           "parent_id", "revoked_at"},
                "INSERT": {"delegation_id", "grantor_id", "grantee_id", "tenant_id", "agent_id",
                           "task_id", "lease_generation", "scopes_json", "issued_at", "expires_at",
                           "parent_id"},
                "UPDATE": {"revoked_at"},
            },
            "workload_delegation_events": {
                "SELECT": {"event_id", "event_type", "delegation_id"},
                "INSERT": {"event_type", "delegation_id", "actor_id", "task_id", "scopes_json", "created_at"},
                "UPDATE": set(),
            },
        }
        with psycopg.connect(self.admin_url) as conn:
            for table, privileges in expected.items():
                for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                    self.assertFalse(conn.execute(
                        "SELECT has_table_privilege(%s,%s,%s)",
                        (self.role, "public." + table, privilege),
                    ).fetchone()[0], (table, privilege))
                columns = conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name=%s", (table,)
                ).fetchall()
                for (column,) in columns:
                    for privilege, allowed in privileges.items():
                        actual = conn.execute(
                            "SELECT has_column_privilege(%s,%s,%s,%s)",
                            (self.role, "public." + table, column, privilege),
                        ).fetchone()[0]
                        self.assertEqual(actual, column in allowed, (table, column, privilege))
            self.assertTrue(conn.execute(
                "SELECT has_sequence_privilege(%s,%s,'USAGE')",
                (self.role, "public.workload_delegation_events_event_id_seq"),
            ).fetchone()[0])
            self.assertFalse(conn.execute(
                "SELECT has_sequence_privilege(%s,%s,'UPDATE')",
                (self.role, "public.workload_delegation_events_event_id_seq"),
            ).fetchone()[0])


if __name__ == "__main__":
    unittest.main()
