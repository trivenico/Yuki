"""PostgreSQL proof that alert operators cannot read delivery secrets."""
import os
import unittest
import uuid

POSTGRES_URL = os.environ.get("YUKI_TEST_POSTGRES_URL")
try:
    import psycopg
    from psycopg import sql
    from psycopg.types.json import Jsonb
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
except ImportError:  # pragma: no cover
    psycopg = None
    sql = None
    Jsonb = None
    conninfo_to_dict = None
    make_conninfo = None

_ENABLED = bool(POSTGRES_URL and psycopg)


@unittest.skipUnless(_ENABLED, "YUKI_TEST_POSTGRES_URL and psycopg are required")
class PostgresAlertOperatorReaderIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        cls.admin_url = POSTGRES_URL
        cls.role = "yuki_alert_operator_" + uuid.uuid4().hex[:12]
        cls.password = uuid.uuid4().hex
        cls.alert_id = uuid.uuid4().hex
        with psycopg.connect(cls.admin_url) as conn:
            migration = root / "migrations/security/0003_security_alert_outbox.sql"
            conn.execute(migration.read_text(encoding="utf-8"), prepare=False)
            conn.execute("CREATE TABLE IF NOT EXISTS yuki_schema_migrations (version TEXT PRIMARY KEY)")
            conn.execute("REVOKE ALL ON security_alert_outbox, yuki_schema_migrations FROM PUBLIC")
            conn.execute(sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT"
            ).format(sql.Identifier(cls.role), sql.Literal(cls.password)))
            conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(cls.role)))
            conn.execute(sql.SQL(
                "GRANT SELECT (alert_id,incident_id,state,attempts,created_at,available_at,"
                "last_error_code,delivered_at,lease_until) ON security_alert_outbox TO {}"
            ).format(sql.Identifier(cls.role)))
            conn.execute(
                "INSERT INTO security_alert_outbox "
                "(alert_id,incident_id,payload,state,attempts,lease_epoch,created_at,available_at,lease_owner,lease_token,lease_until) "
                "VALUES (%s,%s,%s,'inflight',1,1,clock_timestamp(),clock_timestamp(),%s,%s,clock_timestamp()+interval '1 minute')",
                (cls.alert_id, uuid.uuid4().hex, Jsonb({"private": "delivery payload"}), "worker", uuid.uuid4().hex),
            )

    @classmethod
    def tearDownClass(cls):
        if not _ENABLED:
            return
        with psycopg.connect(cls.admin_url) as conn:
            conn.execute("DELETE FROM security_alert_outbox WHERE alert_id=%s", (cls.alert_id,))
            conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(cls.role)))
            conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(cls.role)))

    @classmethod
    def role_url(cls):
        params = conninfo_to_dict(cls.admin_url)
        params.update(user=cls.role, password=cls.password)
        return make_conninfo(**params)

    def setUp(self):
        from core.postgres_security_alert_operator_reader import PostgresSecurityAlertOperatorReader
        self.reader = PostgresSecurityAlertOperatorReader(
            self.role_url(), authorize_operator=lambda value: value == "operator",
            min_pool_size=1, max_pool_size=1,
        )

    def tearDown(self):
        self.reader.close()

    def denied(self, statement):
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with psycopg.connect(self.role_url()) as conn:
                conn.execute(statement)

    def test_operator_can_read_only_minimized_projection_and_aggregate(self):
        rows = self.reader.list_alerts("operator", state="inflight")
        row = next(item for item in rows if item["alert_id"] == self.alert_id)
        self.assertNotIn("payload", row)
        self.assertNotIn("lease_token", row)
        self.assertNotIn("lease_owner", row)
        health = self.reader.health_snapshot("operator")
        self.assertGreaterEqual(health["inflight"], 1)
        self.assertNotIn("payload", health)

    def test_database_role_cannot_read_payload_or_delivery_lease_credentials(self):
        for statement in (
            "SELECT payload FROM security_alert_outbox",
            "SELECT lease_token FROM security_alert_outbox",
            "SELECT lease_owner FROM security_alert_outbox",
            "SELECT * FROM yuki_schema_migrations",
            "SELECT * FROM security_monitor_signals",
        ):
            self.denied(statement)

    def test_operator_role_is_read_only_and_has_exact_column_grants(self):
        for statement in (
            "INSERT INTO security_alert_outbox (alert_id,incident_id,payload) VALUES ('" + "a" * 32 + "','b','{}')",
            "UPDATE security_alert_outbox SET state='delivered'",
            "DELETE FROM security_alert_outbox",
            "TRUNCATE security_alert_outbox",
            "CREATE TABLE public.alert_operator_must_not_create (id integer)",
        ):
            self.denied(statement)
        allowed = {
            "alert_id", "incident_id", "state", "attempts", "created_at",
            "available_at", "last_error_code", "delivered_at", "lease_until",
        }
        with psycopg.connect(self.admin_url) as conn:
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                self.assertFalse(conn.execute(
                    "SELECT has_table_privilege(%s,'public.security_alert_outbox',%s)",
                    (self.role, privilege),
                ).fetchone()[0])
            columns = conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='security_alert_outbox'"
            ).fetchall()
            for (column,) in columns:
                self.assertEqual(
                    conn.execute(
                        "SELECT has_column_privilege(%s,'public.security_alert_outbox',%s,'SELECT')",
                        (self.role, column),
                    ).fetchone()[0],
                    column in allowed, column,
                )


if __name__ == "__main__":
    unittest.main()
