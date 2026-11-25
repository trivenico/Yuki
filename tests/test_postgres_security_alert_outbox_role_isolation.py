"""Disposable PostgreSQL tests for the security alert outbox service role."""
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
class PostgresAlertOutboxRoleIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        cls.admin_url = POSTGRES_URL
        cls.role = "yuki_alert_outbox_" + uuid.uuid4().hex[:12]
        cls.password = uuid.uuid4().hex
        with psycopg.connect(cls.admin_url) as conn:
            migration = root / "migrations/security/0003_security_alert_outbox.sql"
            conn.execute(migration.read_text(encoding="utf-8"), prepare=False)
            conn.execute("CREATE TABLE IF NOT EXISTS yuki_schema_migrations (version TEXT PRIMARY KEY)")
            conn.execute("TRUNCATE security_alert_outbox")
            conn.execute("REVOKE ALL ON security_alert_outbox, yuki_schema_migrations FROM PUBLIC")
            conn.execute(sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT"
            ).format(sql.Identifier(cls.role), sql.Literal(cls.password)))
            conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(cls.role)))
            # SELECT includes the payload only because the delivery worker must
            # return it from claim(); operator APIs expose a separate projection.
            conn.execute(sql.SQL(
                "GRANT SELECT (alert_id,incident_id,payload,state,attempts,lease_epoch,"
                "created_at,available_at,lease_owner,lease_token,lease_until,last_error_code,delivered_at) "
                "ON security_alert_outbox TO {}"
            ).format(sql.Identifier(cls.role)))
            conn.execute(sql.SQL(
                "GRANT INSERT (alert_id,incident_id,payload,state,created_at,available_at) "
                "ON security_alert_outbox TO {}"
            ).format(sql.Identifier(cls.role)))
            conn.execute(sql.SQL(
                "GRANT UPDATE (state,attempts,lease_epoch,lease_owner,lease_token,lease_until,"
                "last_error_code,delivered_at,available_at) ON security_alert_outbox TO {}"
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
        from core.postgres_security_alert_outbox import PostgresSecurityAlertOutbox
        self.store = PostgresSecurityAlertOutbox(
            self.role_url(), authorize_operator=lambda value: value == "operator",
            base_retry_seconds=1, max_retry_seconds=4, max_attempts=2,
            lease_seconds=5, min_pool_size=1, max_pool_size=1,
        )
        self.alert_ids = []

    def tearDown(self):
        self.store.close()
        if self.alert_ids:
            with psycopg.connect(self.admin_url) as conn:
                conn.execute(
                    "DELETE FROM security_alert_outbox WHERE alert_id = ANY(%s)",
                    (self.alert_ids,),
                )

    @staticmethod
    def incident():
        return {
            "incident_id": uuid.uuid4().hex,
            "category": "audit_integrity_failure",
            "severity": "critical",
            "status": "open",
            "first_seen": 1000.0,
            "last_seen": 1000.0,
            "event_count": 1,
            "last_reason_code": "audit_chain_invalid",
        }

    def enqueue(self):
        alert_id = self.store.enqueue_incident(self.incident())
        self.alert_ids.append(alert_id)
        return alert_id

    def assert_denied(self, statement):
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with psycopg.connect(self.role_url()) as conn:
                conn.execute(statement)

    def test_runtime_enqueue_claim_retry_and_acknowledgement_use_minimum_role(self):
        alert_id = self.enqueue()
        first = self.store.claim("delivery_worker")
        self.assertEqual(first["alert_id"], alert_id)
        self.assertIn("incident_id", first["payload"])
        self.assertEqual(
            self.store.complete(
                "delivery_worker", alert_id, lease_token=first["lease_token"],
                lease_epoch=first["lease_epoch"], success=False,
                error_code="provider_unavailable",
            ),
            "pending",
        )
        with psycopg.connect(self.admin_url) as conn:
            conn.execute(
                "UPDATE security_alert_outbox SET available_at=clock_timestamp()-interval '1 second' "
                "WHERE alert_id=%s", (alert_id,),
            )
        second = self.store.claim("delivery_worker")
        self.assertEqual(second["attempt"], 2)
        self.assertEqual(
            self.store.complete(
                "delivery_worker", alert_id, lease_token=second["lease_token"],
                lease_epoch=second["lease_epoch"], success=True,
            ),
            "delivered",
        )
        listed = self.store.list_alerts("operator", state="delivered")
        self.assertEqual([row["alert_id"] for row in listed], [alert_id])
        self.assertNotIn("payload", listed[0])
        health = self.store.health_snapshot("operator")
        self.assertGreaterEqual(health["delivered"], 1)
        self.assertNotIn("payload", health)

    def test_operator_projection_is_authorized_and_does_not_return_payload(self):
        alert_id = self.enqueue()
        rows = self.store.list_alerts("operator")
        row = next(item for item in rows if item["alert_id"] == alert_id)
        self.assertNotIn("payload", row)
        self.assertNotIn("lease_token", row)
        with self.assertRaises(PermissionError):
            self.store.list_alerts("attacker")
        with self.assertRaises(PermissionError):
            self.store.health_snapshot("attacker")

    def test_role_cannot_delete_truncate_or_change_schema_or_read_migration_ledger(self):
        for statement in (
            "DELETE FROM security_alert_outbox",
            "TRUNCATE security_alert_outbox",
            "UPDATE security_alert_outbox SET alert_id='00000000000000000000000000000000'",
            "CREATE TABLE public.alert_role_must_not_create (id integer)",
            "ALTER TABLE security_alert_outbox ADD COLUMN unsafe text",
            "SELECT * FROM yuki_schema_migrations",
        ):
            self.assert_denied(statement)

    def test_role_has_only_column_scoped_privileges(self):
        expected_select = {
            "alert_id", "incident_id", "payload", "state", "attempts", "lease_epoch",
            "created_at", "available_at", "lease_owner", "lease_token", "lease_until",
            "last_error_code", "delivered_at",
        }
        expected_insert = {
            "alert_id", "incident_id", "payload", "state", "created_at", "available_at",
        }
        expected_update = {
            "state", "attempts", "lease_epoch", "lease_owner", "lease_token",
            "lease_until", "last_error_code", "delivered_at", "available_at",
        }
        with psycopg.connect(self.admin_url) as conn:
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                self.assertFalse(conn.execute(
                    "SELECT has_table_privilege(%s,'public.security_alert_outbox',%s)",
                    (self.role, privilege),
                ).fetchone()[0], privilege)
            columns = conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='security_alert_outbox'"
            ).fetchall()
            for (column,) in columns:
                for privilege, expected in (
                    ("SELECT", column in expected_select),
                    ("INSERT", column in expected_insert),
                    ("UPDATE", column in expected_update),
                ):
                    actual = conn.execute(
                        "SELECT has_column_privilege(%s,'public.security_alert_outbox',%s,%s)",
                        (self.role, column, privilege),
                    ).fetchone()[0]
                    self.assertEqual(actual, expected, (column, privilege))
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                self.assertFalse(conn.execute(
                    "SELECT has_table_privilege(%s,'public.yuki_schema_migrations',%s)",
                    (self.role, privilege),
                ).fetchone()[0], privilege)


if __name__ == "__main__":
    unittest.main()
