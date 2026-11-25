"""Disposable PostgreSQL tests for the security monitor service role."""
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
class PostgresSecurityMonitorRoleIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        cls.admin_url = POSTGRES_URL
        cls.role = "yuki_security_monitor_" + uuid.uuid4().hex[:12]
        cls.password = uuid.uuid4().hex
        with psycopg.connect(cls.admin_url) as conn:
            migration = root / "migrations/security/0005_security_monitor.sql"
            conn.execute(migration.read_text(encoding="utf-8"), prepare=False)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS yuki_schema_migrations (version TEXT PRIMARY KEY)"
            )
            conn.execute("TRUNCATE security_monitor_incident_history, security_monitor_incidents, security_monitor_signals CASCADE")
            conn.execute(
                "REVOKE ALL ON security_monitor_signals, security_monitor_incidents, "
                "security_monitor_incident_history, yuki_schema_migrations FROM PUBLIC"
            )
            conn.execute(sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT"
            ).format(sql.Identifier(cls.role), sql.Literal(cls.password)))
            conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(cls.role)))
            # Signals are append-only. Aggregation reads only these indexed
            # dimensions; readiness and INSERT RETURNING use event_id.
            conn.execute(sql.SQL(
                "GRANT SELECT (event_id,category,subject_hash,occurred_at) "
                "ON security_monitor_signals TO {}"
            ).format(sql.Identifier(cls.role)))
            conn.execute(sql.SQL(
                "GRANT INSERT (signal_id,category,occurred_at,reason_code,subject_hash,source) "
                "ON security_monitor_signals TO {}"
            ).format(sql.Identifier(cls.role)))
            # Incident upsert returns the full row and compares prior state;
            # the repository's operator list currently selects all columns.
            conn.execute(sql.SQL(
                "GRANT SELECT (incident_id,fingerprint,category,severity,status,first_seen,"
                "last_seen,event_count,last_reason_code,updated_by) "
                "ON security_monitor_incidents TO {}"
            ).format(sql.Identifier(cls.role)))
            conn.execute(sql.SQL(
                "GRANT INSERT (incident_id,fingerprint,category,severity,status,first_seen,"
                "last_seen,event_count,last_reason_code) ON security_monitor_incidents TO {}"
            ).format(sql.Identifier(cls.role)))
            conn.execute(sql.SQL(
                "GRANT UPDATE (first_seen,last_seen,event_count,last_reason_code,status,updated_by) "
                "ON security_monitor_incidents TO {}"
            ).format(sql.Identifier(cls.role)))
            conn.execute(sql.SQL(
                "GRANT SELECT (history_id) ON security_monitor_incident_history TO {}"
            ).format(sql.Identifier(cls.role)))
            conn.execute(sql.SQL(
                "GRANT INSERT (incident_id,action,operator_hash,occurred_at) "
                "ON security_monitor_incident_history TO {}"
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
        from core.postgres_security_event_monitor import PostgresSecurityEventMonitor
        self.monitor = PostgresSecurityEventMonitor(
            self.role_url(), authorize_operator=lambda value: value == "security-operator",
            failure_threshold=2, min_pool_size=1, max_pool_size=1,
        )

    def tearDown(self):
        self.monitor.close()

    def test_runtime_record_aggregate_transition_and_readiness_use_minimum_role(self):
        from core.security_event_monitor import SecuritySignal
        subject = uuid.uuid4().hex * 2
        first = self.monitor.record(SecuritySignal(
            "monitor-role-" + uuid.uuid4().hex, "authorization_denied",
            1700.0, "policy_denied", subject, "authorization_audit",
        ))
        self.assertIsNone(first)
        incident = self.monitor.record(SecuritySignal(
            "monitor-role-" + uuid.uuid4().hex, "authorization_denied",
            1701.0, "policy_denied", subject, "authorization_audit",
        ))
        self.assertIsNotNone(incident)
        self.assertTrue(self.monitor.check_ready())
        rows = self.monitor.incidents("security-operator", status="open")
        self.assertIn(incident["incident_id"], {row["incident_id"] for row in rows})
        self.monitor.transition("security-operator", incident["incident_id"], "acknowledge")
        acknowledged = self.monitor.incidents("security-operator", status="acknowledged")
        self.assertIn(incident["incident_id"], {row["incident_id"] for row in acknowledged})

    def assert_denied(self, statement):
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with psycopg.connect(self.role_url()) as conn:
                conn.execute(statement)

    def test_role_cannot_mutate_immutable_rows_or_access_other_security_domains(self):
        for statement in (
            "UPDATE security_monitor_signals SET reason_code='tampered'",
            "DELETE FROM security_monitor_signals",
            "DELETE FROM security_monitor_incident_history",
            "TRUNCATE security_monitor_signals",
            "CREATE TABLE public.monitor_role_must_not_create (id integer)",
            "ALTER TABLE security_monitor_incidents ADD COLUMN unsafe text",
            "SELECT * FROM security_ingestion_cursors",
            "SELECT * FROM security_alert_outbox",
            "SELECT * FROM security_work_leases",
            "SELECT * FROM yuki_schema_migrations",
        ):
            self.assert_denied(statement)

    def test_role_has_only_column_scoped_mutation_privileges(self):
        with psycopg.connect(self.admin_url) as conn:
            for relation in (
                "security_monitor_signals",
                "security_monitor_incidents",
                "security_monitor_incident_history",
            ):
                for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                    self.assertFalse(conn.execute(
                        "SELECT has_table_privilege(%s,%s,%s)",
                        (self.role, "public." + relation, privilege),
                    ).fetchone()[0], (relation, privilege))
            self.assertFalse(conn.execute(
                "SELECT has_table_privilege(%s,'public.yuki_schema_migrations','SELECT')",
                (self.role,),
            ).fetchone()[0])


if __name__ == "__main__":
    unittest.main()
