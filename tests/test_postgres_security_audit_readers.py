"""Contract tests for narrow PostgreSQL security audit readers."""
import unittest
from contextlib import contextmanager

from core.postgres_security_audit_readers import (
    PostgresAuthorizationAuditReader,
    PostgresWorkloadKeyAuditReader,
)


class FakeConnection:
    def __init__(self):
        self.statements = []

    @contextmanager
    def transaction(self):
        yield self

    def execute(self, sql, params=()):
        self.statements.append((sql, params))
        class Result:
            def fetchall(self):
                return []
        return Result()


class FakePool:
    def __init__(self, connection):
        self.value = connection

    @contextmanager
    def connection(self):
        yield self.value


class PostgresSecurityAuditReaderContractTests(unittest.TestCase):
    def test_reader_executes_each_query_in_read_only_transaction(self):
        connection = FakeConnection()
        reader = PostgresAuthorizationAuditReader.__new__(PostgresAuthorizationAuditReader)
        reader._pool = FakePool(connection)
        self.assertEqual(reader.event_page(limit=10), ())
        self.assertEqual(connection.statements[0][0], "SET TRANSACTION READ ONLY")
        self.assertIn("SELECT event_id", connection.statements[1][0])

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
        with self.assertRaisesRegex(ValueError, "page_invalid"):
            reader.audit_events(limit=5001)
        self.assertEqual(reader.audit_events(after_event_id=0, limit=1), ())

    def test_authorization_reader_rejects_unbounded_or_invalid_cursor(self):
        reader = PostgresAuthorizationAuditReader.__new__(PostgresAuthorizationAuditReader)
        with self.assertRaisesRegex(ValueError, "page_invalid"):
            reader.event_page(after_event_id=-1)
        with self.assertRaisesRegex(ValueError, "page_invalid"):
            reader.event_page(limit=0)

    def test_postgres_monitor_reads_projection_without_administrator_identity(self):
        from core.postgres_security_event_monitor import PostgresSecurityEventMonitor

        class Projection:
            def audit_events(self, *, after_event_id=0, limit=500):
                return ({
                    "event_id": 7, "administrator": "recorded-actor",
                    "issuer": "issuer", "key_id": "key-7", "action": "revoked",
                    "occurred_at": 123.0,
                },)

        monitor = PostgresSecurityEventMonitor.__new__(PostgresSecurityEventMonitor)
        recorded = []
        monitor.record = recorded.append
        highest = monitor.ingest_key_lifecycle(Projection(), after_event_id=6, limit=10)
        self.assertEqual(highest, 7)
        self.assertEqual(recorded[0].signal_id, "workload-key-audit:7")


if __name__ == "__main__":
    unittest.main()
