import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from core.security_event_monitor_factory import close_security_event_monitor, create_security_event_monitor


class FakePostgresMonitor:
    instances = []
    def __init__(self, database_url, **kwargs):
        self.database_url, self.kwargs = database_url, kwargs
        self.ready, self.closed = True, False
        self.__class__.instances.append(self)
    def check_ready(self):
        if not self.ready:
            raise RuntimeError("schema_missing")
        return True
    def close(self):
        self.closed = True


class SecurityEventMonitorFactoryTests(unittest.TestCase):
    def setUp(self):
        FakePostgresMonitor.instances = []
    def module(self, cls=FakePostgresMonitor):
        value = types.ModuleType("core.postgres_security_event_monitor")
        value.PostgresSecurityEventMonitor = cls
        return value
    def test_sqlite_factory_and_shutdown_hook(self):
        with tempfile.TemporaryDirectory() as directory:
            monitor = create_security_event_monitor(
                {"storage_backend": "sqlite", "workspace": directory},
                authorize_operator=lambda value: value == "ops",
            )
            self.assertEqual(monitor.database_path, str(Path(directory) / "security-events.sqlite3"))
            close_security_event_monitor(monitor)
    def test_postgres_factory_checks_readiness_and_closes(self):
        authorizer = lambda value: value == "ops"
        with patch.dict(sys.modules, {"core.postgres_security_event_monitor": self.module()}):
            monitor = create_security_event_monitor({
                "storage_backend": "postgres",
                "database_url": "postgresql://user:pass@localhost/yuki",
                "database_pool_min": 2, "database_pool_max": 6,
                "security_failure_threshold": 7, "security_window_seconds": 120,
            }, authorize_operator=authorizer)
        self.assertIs(monitor.kwargs["authorize_operator"], authorizer)
        self.assertEqual(monitor.kwargs["failure_threshold"], 7)
        self.assertEqual(monitor.kwargs["window_seconds"], 120)
        close_security_event_monitor(monitor)
        self.assertTrue(monitor.closed)
    def test_postgres_missing_url_fails_without_sqlite_fallback(self):
        with self.assertRaisesRegex(ValueError, "database_url_required"):
            create_security_event_monitor(
                {"storage_backend": "postgres", "workspace": "/tmp"},
                authorize_operator=lambda _: True,
            )
        self.assertEqual(FakePostgresMonitor.instances, [])
    def test_postgres_schema_failure_closes_pool_and_fails_startup(self):
        class SchemaFailure(FakePostgresMonitor):
            def check_ready(self):
                raise RuntimeError("migration_missing")
        with patch.dict(sys.modules, {"core.postgres_security_event_monitor": self.module(SchemaFailure)}):
            with self.assertRaisesRegex(RuntimeError, "unavailable or not migrated"):
                create_security_event_monitor({
                    "storage_backend": "postgres",
                    "database_url": "postgresql://user:pass@localhost/yuki",
                }, authorize_operator=lambda _: True)
        self.assertTrue(SchemaFailure.instances[-1].closed)
    def test_rejects_invalid_backend_and_pool_bounds(self):
        with self.assertRaisesRegex(ValueError, "storage_backend"):
            create_security_event_monitor({"storage_backend": "mysql"}, authorize_operator=lambda _: True)
        with patch.dict(sys.modules, {"core.postgres_security_event_monitor": self.module()}):
            with self.assertRaisesRegex(ValueError, "pool_configuration_invalid"):
                create_security_event_monitor({
                    "storage_backend": "postgres",
                    "database_url": "postgresql://user:pass@localhost/yuki",
                    "database_pool_min": 7, "database_pool_max": 2,
                }, authorize_operator=lambda _: True)
        self.assertEqual(FakePostgresMonitor.instances, [])


if __name__ == "__main__":
    unittest.main()
