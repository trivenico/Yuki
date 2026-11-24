import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from core.authorization_audit_factory import (
    close_authorization_audit_repository, create_authorization_audit_repository,
)


class FakePostgresAudit:
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


class AuthorizationAuditFactoryTests(unittest.TestCase):
    def setUp(self):
        FakePostgresAudit.instances = []

    def test_sqlite_factory_and_close_hook(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = create_authorization_audit_repository({"workspace": directory})
            self.assertEqual(repository.database_path, str(Path(directory) / "authorization-audit.sqlite3"))
            close_authorization_audit_repository(repository)

    def test_postgres_factory_checks_readiness_and_closes(self):
        module = types.ModuleType("core.postgres_authorization_audit")
        module.PostgresAuthorizationAuditLog = FakePostgresAudit
        with patch.dict(sys.modules, {"core.postgres_authorization_audit": module}):
            repository = create_authorization_audit_repository({
                "storage_backend": "postgres", "database_url": "postgresql://localhost/yuki",
                "database_pool_min": 2, "database_pool_max": 5,
            })
            self.assertEqual(repository.kwargs["max_pool_size"], 5)
            close_authorization_audit_repository(repository)
            self.assertTrue(repository.closed)

    def test_postgres_schema_failure_closes_pool_and_fails_closed(self):
        class Broken(FakePostgresAudit):
            def check_ready(self):
                raise RuntimeError("schema_missing")
        module = types.ModuleType("core.postgres_authorization_audit")
        module.PostgresAuthorizationAuditLog = Broken
        with patch.dict(sys.modules, {"core.postgres_authorization_audit": module}):
            with self.assertRaisesRegex(RuntimeError, "not_ready"):
                create_authorization_audit_repository({
                    "storage_backend": "postgres", "database_url": "postgresql://localhost/yuki",
                })
        self.assertTrue(Broken.instances[-1].closed)

    def test_unknown_backend_and_missing_database_url_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "storage_backend_invalid"):
            create_authorization_audit_repository({"storage_backend": "other"})
        with self.assertRaisesRegex(ValueError, "database_url_required"):
            create_authorization_audit_repository({"storage_backend": "postgres"})


if __name__ == "__main__":
    unittest.main()
