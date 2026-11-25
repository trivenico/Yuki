"""Real PostgreSQL guard tests for the composed production security runtime."""
import os
from pathlib import Path
import unittest

POSTGRES_URL = os.environ.get("YUKI_TEST_POSTGRES_URL")
try:
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None

_ENABLED = bool(POSTGRES_URL and psycopg)


@unittest.skipUnless(_ENABLED, "YUKI_TEST_POSTGRES_URL and psycopg are required")
class PostgresProductionSecurityRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        migrations = (
            "0001_security_work_leases.sql",
            "0002_security_ingestion_cursors.sql",
            "0003_security_alert_outbox.sql",
            "0004_workload_key_registry.sql",
            "0005_security_monitor.sql",
            "0006_authorization_audit.sql",
        )
        with psycopg.connect(POSTGRES_URL) as conn:
            for name in migrations:
                conn.execute((root / "migrations/security" / name).read_text(encoding="utf-8"), prepare=False)

    def test_runtime_rejects_legacy_shared_database_url(self):
        """A single DSN must never silently collapse the eight service identities."""
        from core.production_security_runtime import create_production_security_runtime

        class ForbiddenConstruction:
            def authorize(self, _request):
                raise AssertionError("policy must not be reached before identity validation")

        with self.assertRaisesRegex(ValueError, "database_urls_required"):
            create_production_security_runtime(
                {
                    "storage_backend": "postgres",
                    "database_url": POSTGRES_URL,
                    "database_pool_min": 1,
                    "database_pool_max": 2,
                    "database_pool_timeout": 10.0,
                },
                policy=ForbiddenConstruction(),
                audit_anchor=object(),
                principal_for_actor=lambda actor: actor,
                principal_for_worker=lambda worker: worker,
                policy_revision="revision-1",
                authorize_admin=lambda _operator: True,
                authorize_operator=lambda _operator: True,
            )


if __name__ == "__main__":
    unittest.main()
