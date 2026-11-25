"""Contract tests for narrow PostgreSQL security audit readers."""
import unittest

from core.postgres_security_audit_readers import (
    PostgresAuthorizationAuditReader,
    PostgresWorkloadKeyAuditReader,
)


class PostgresSecurityAuditReaderContractTests(unittest.TestCase):
    def test_authorization_reader_exposes_only_bounded_event_projection(self):
        reader = PostgresAuthorizationAuditReader.__new__(PostgresAuthorizationAuditReader)
        self.assertTrue(callable(getattr(reader, "event_page", None)))
        self.assertTrue(callable(getattr(reader, "check_ready", None)))
        for forbidden in ("__call__", "register", "activate", "retire", "revoke",
                          "publish_checkpoint", "import_sqlite_history"):
            self.assertFalse(hasattr(reader, forbidden), forbidden)

    def test_workload_key_reader_exposes_only_audit_projection(self):
        reader = PostgresWorkloadKeyAuditReader.__new__(PostgresWorkloadKeyAuditReader)
        self.assertTrue(callable(getattr(reader, "audit_events", None)))
        self.assertTrue(callable(getattr(reader, "check_ready", None)))
        for forbidden in ("register", "activate", "retire", "revoke", "resolve"):
            self.assertFalse(hasattr(reader, forbidden), forbidden)

    def test_workload_key_reader_requires_worker_identity_and_bounded_page(self):
        reader = PostgresWorkloadKeyAuditReader.__new__(PostgresWorkloadKeyAuditReader)
        with self.assertRaisesRegex(ValueError, "worker_invalid"):
            reader.audit_events("", limit=1)
        with self.assertRaisesRegex(ValueError, "page_invalid"):
            reader.audit_events("ingestion-worker", limit=5001)

    def test_authorization_reader_rejects_unbounded_or_invalid_cursor(self):
        reader = PostgresAuthorizationAuditReader.__new__(PostgresAuthorizationAuditReader)
        with self.assertRaisesRegex(ValueError, "page_invalid"):
            reader.event_page(after_event_id=-1)
        with self.assertRaisesRegex(ValueError, "page_invalid"):
            reader.event_page(limit=0)


if __name__ == "__main__":
    unittest.main()
