"""Real PostgreSQL smoke tests for the composed production security runtime."""
import os
from pathlib import Path
import unittest
import uuid

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

    def test_runtime_constructs_checks_and_closes_shared_repositories(self):
        from datetime import datetime, timezone
        from core.authorization import AuthorizationDecision, Principal
        from core.postgres_authorization_audit import PostgresAuthorizationAuditLog
        from core.production_security_runtime import create_production_security_runtime

        audit = PostgresAuthorizationAuditLog(POSTGRES_URL)
        checkpoint = audit.checkpoint()
        audit.close()

        class MemoryAnchor:
            def latest(self):
                return checkpoint
            def publish(self, value):
                raise AssertionError("runtime startup must not mutate external anchor")

        class Policy:
            def authorize(self, request):
                return AuthorizationDecision(True, "runtime-smoke", "revision-1", "allowed")

        runtime = create_production_security_runtime(
            {
                "storage_backend": "postgres",
                "database_url": POSTGRES_URL,
                "database_pool_min": 1,
                "database_pool_max": 2,
                "database_pool_timeout": 10.0,
                "audit_checkpoint_max_age_seconds": 300,
            },
            policy=Policy(),
            audit_anchor=MemoryAnchor(),
            principal_for_actor=lambda actor: Principal(actor, "human"),
            principal_for_worker=lambda worker: Principal(worker, "workload", "tenant-runtime", "agent-runtime"),
            policy_revision="revision-1",
            authorize_admin=lambda _operator: True,
            authorize_operator=lambda _operator: True,
            key_audit_operator="runtime-security-admin",
        )
        try:
            runtime.verify_readiness()
            self.assertEqual(runtime.ingest_authorization_page("runtime-worker-" + uuid.uuid4().hex[:12])["status"], "ok")
        finally:
            runtime.close()


if __name__ == "__main__":
    unittest.main()
