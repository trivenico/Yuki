"""Disposable PostgreSQL LOGIN-role tests for the workflow repository."""
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
class PostgresWorkflowRoleIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.admin_url = POSTGRES_URL
        cls.role = "yuki_workflow_" + uuid.uuid4().hex[:12]
        cls.password = uuid.uuid4().hex
        migrations = (
            "0001_security_work_leases.sql", "0003_security_alert_outbox.sql",
            "0005_security_monitor.sql", "0007_workflow_runs.sql",
            "0008_workload_delegations.sql", "0009_external_operations.sql",
            "0010_workflow_task_ownership.sql",
            "0020_workflow_dispatch_visibility_gate.sql",
            "0021_distributed_workflow_goals.sql",
            "0022_workflow_dependency_readiness.sql",
            "0023_workflow_lease_generation_nullable.sql",
        )
        with psycopg.connect(cls.admin_url) as conn:
            for migration_name in migrations:
                if migration_name == "0010_workflow_task_ownership.sql":
                    already_applied = conn.execute(
                        "SELECT 1 FROM pg_constraint WHERE conname=%s",
                        ("workflow_runs_ownership_status_check",),
                    ).fetchone()
                    if already_applied:
                        continue
                migration = root / "migrations/security" / migration_name
                conn.execute(migration.read_text(encoding="utf-8"), prepare=False)
            conn.execute("CREATE TABLE IF NOT EXISTS yuki_schema_migrations (version TEXT PRIMARY KEY)")
            conn.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
            conn.execute(
                "TRUNCATE external_operation_events, external_operations, task_events, workflow_runs, "
                "workload_delegation_events, workload_delegations RESTART IDENTITY"
            )
            conn.execute(
                "REVOKE ALL ON workflow_runs, task_events, workload_delegations, "
                "workload_delegation_events, yuki_schema_migrations, security_alert_outbox, "
                "security_monitor_incidents, security_work_leases FROM PUBLIC"
            )
            conn.execute("REVOKE ALL ON SEQUENCE task_events_event_id_seq FROM PUBLIC")
            conn.execute(sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT"
            ).format(sql.Identifier(cls.role), sql.Literal(cls.password)))
            conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(cls.role)))

            workflow_select = [
                "run_id", "idempotency_key", "request_digest", "workflow_name",
                "definition_json", "state", "result_json", "error_code", "created_at",
                "updated_at", "version", "attempts", "lease_owner", "lease_expires_at",
                "lease_generation", "approved_by", "approved_at", "approval_digest",
                "approved_session_hash", "approval_id", "tenant_id",
                "owner_principal_id", "ownership_status",
            ]
            workflow_insert = [
                "run_id", "idempotency_key", "request_digest", "workflow_name",
                "definition_json", "state", "tenant_id", "owner_principal_id",
                "ownership_status",
            ]
            workflow_update = [
                "state", "result_json", "error_code", "updated_at", "version",
                "attempts", "lease_owner", "lease_expires_at", "lease_generation",
                "approved_by", "approved_at", "approval_digest",
                "approved_session_hash", "approval_id",
            ]
            conn.execute(sql.SQL(
                "GRANT SELECT ({}) ON workflow_runs TO {}"
            ).format(sql.SQL(",").join(map(sql.Identifier, workflow_select)), sql.Identifier(cls.role)))
            conn.execute(sql.SQL(
                "GRANT INSERT ({}) ON workflow_runs TO {}"
            ).format(sql.SQL(",").join(map(sql.Identifier, workflow_insert)), sql.Identifier(cls.role)))
            conn.execute(sql.SQL(
                "GRANT UPDATE ({}) ON workflow_runs TO {}"
            ).format(sql.SQL(",").join(map(sql.Identifier, workflow_update)), sql.Identifier(cls.role)))
            event_select = ["event_id", "task_id", "sequence", "kind", "actor_id", "payload_json", "created_at"]
            event_insert = ["task_id", "sequence", "kind", "actor_id", "payload_json", "created_at"]
            conn.execute(sql.SQL(
                "GRANT SELECT ({}) ON task_events TO {}"
            ).format(sql.SQL(",").join(map(sql.Identifier, event_select)), sql.Identifier(cls.role)))
            conn.execute(sql.SQL(
                "GRANT INSERT ({}) ON task_events TO {}"
            ).format(sql.SQL(",").join(map(sql.Identifier, event_insert)), sql.Identifier(cls.role)))
            conn.execute(sql.SQL(
                "GRANT USAGE ON SEQUENCE task_events_event_id_seq TO {}"
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
        from automation.postgres_workflow_store import PostgresWorkflowRunStore
        self.store = PostgresWorkflowRunStore(self.role_url(), min_pool_size=1, max_pool_size=1)
        self.key = "role-isolation-" + uuid.uuid4().hex

    def tearDown(self):
        self.store.close()
        with psycopg.connect(self.admin_url) as conn:
            conn.execute("DELETE FROM workflow_runs WHERE idempotency_key=%s", (self.key,))

    def test_repository_lifecycle_works_with_column_scoped_login_role(self):
        definition = [{"name": "reviewed-step", "action": "emit",
                       "argument": "bounded", "requires_approval": True}]
        created = self.store.create(
            idempotency_key=self.key, workflow_name="role-isolation",
            definition=definition, tenant_id="tenant-role-test",
            owner_principal_id="human:reviewer",
        )
        self.assertEqual(created.state, "queued")
        self.assertEqual(created.ownership_status, "owned")
        self.assertEqual(self.store.get(created.run_id).run_id, created.run_id)
        self.assertEqual(self.store.get_by_idempotency_key(self.key).run_id, created.run_id)
        self.assertEqual([item.run_id for item in self.store.list_runs(state="queued") if item.run_id == created.run_id], [created.run_id])

        approved = self.store.approve_with_event(
            created.run_id, actor_id="human:reviewer", session_id="session-role-test",
            expected_version=created.version,
        )
        self.assertEqual(approved.approved_by, "human:reviewer")
        self.assertEqual([event.kind for event in self.store.list_task_events(created.run_id)],
                         ["task.approved"])

        claimed = self.store.claim_next(worker_id="worker-role-test")
        self.assertEqual(claimed.run_id, created.run_id)
        self.assertTrue(claimed.lease_generation)
        event = self.store.append_task_event(
            claimed.run_id, kind="task.progress", actor_id="worker-role-test",
            payload={"stage": "running"}, expected_version=claimed.version,
            expected_lease_owner="worker-role-test",
            expected_lease_generation=claimed.lease_generation,
        )
        self.assertEqual(event.kind, "task.progress")
        current = self.store.get(claimed.run_id)
        completed = self.store.transition_with_event(
            current.run_id, expected_version=current.version, to_state="succeeded",
            event_kind="task.completed", actor_id="worker-role-test",
            event_payload={"result": "ok"}, result={"ok": True},
            expected_lease_owner="worker-role-test",
            expected_lease_generation=claimed.lease_generation,
        )
        self.assertEqual(completed.state, "succeeded")
        self.assertEqual([e.kind for e in self.store.list_task_events(created.run_id)],
                         ["task.approved", "task.progress", "task.completed"])

    def assert_denied(self, statement):
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with psycopg.connect(self.role_url()) as conn:
                conn.execute(statement)

    def test_workflow_role_cannot_access_delegation_security_or_migration_relations(self):
        for statement in (
            "SELECT * FROM workload_delegations",
            "SELECT * FROM workload_delegation_events",
            "SELECT * FROM yuki_schema_migrations",
            "SELECT * FROM security_alert_outbox",
            "SELECT * FROM security_monitor_incidents",
            "SELECT * FROM security_work_leases",
            "DELETE FROM workflow_runs",
            "DELETE FROM task_events",
            "TRUNCATE workflow_runs",
            "UPDATE task_events SET actor_id='tampered'",
            "CREATE TABLE public.workflow_role_must_not_create (id integer)",
            "ALTER TABLE workflow_runs ADD COLUMN unsafe text",
        ):
            self.assert_denied(statement)

    def test_role_has_only_declared_column_and_sequence_privileges(self):
        expected = {
            "workflow_runs": {
                "SELECT": {"run_id", "idempotency_key", "request_digest", "workflow_name",
                           "definition_json", "state", "result_json", "error_code", "created_at",
                           "updated_at", "version", "attempts", "lease_owner", "lease_expires_at",
                           "lease_generation", "approved_by", "approved_at", "approval_digest",
                           "approved_session_hash", "approval_id", "tenant_id",
                           "owner_principal_id", "ownership_status"},
                "INSERT": {"run_id", "idempotency_key", "request_digest", "workflow_name",
                           "definition_json", "state", "tenant_id", "owner_principal_id",
                           "ownership_status"},
                "UPDATE": {"state", "result_json", "error_code", "updated_at", "version",
                           "attempts", "lease_owner", "lease_expires_at", "lease_generation",
                           "approved_by", "approved_at", "approval_digest",
                           "approved_session_hash", "approval_id"},
            },
            "task_events": {
                "SELECT": {"event_id", "task_id", "sequence", "kind", "actor_id", "payload_json", "created_at"},
                "INSERT": {"task_id", "sequence", "kind", "actor_id", "payload_json", "created_at"},
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
                (self.role, "public.task_events_event_id_seq"),
            ).fetchone()[0])
            self.assertFalse(conn.execute(
                "SELECT has_sequence_privilege(%s,%s,'UPDATE')",
                (self.role, "public.task_events_event_id_seq"),
            ).fetchone()[0])


if __name__ == "__main__":
    unittest.main()
