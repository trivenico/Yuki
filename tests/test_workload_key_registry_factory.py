import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from core.workload_key_registry_factory import (
    close_workload_key_registry,
    create_workload_key_registry,
)


class FakePostgresRegistry:
    instances = []

    def __init__(self, database_url, *, authorize_admin, min_pool_size, max_pool_size):
        self.database_url = database_url
        self.authorize_admin = authorize_admin
        self.min_pool_size = min_pool_size
        self.max_pool_size = max_pool_size
        self.ready = True
        self.closed = False
        self.__class__.instances.append(self)

    def check_ready(self):
        if self.ready is not True:
            raise RuntimeError("schema_not_ready")
        return True

    def close(self):
        self.closed = True


class WorkloadKeyRegistryFactoryTests(unittest.TestCase):
    def setUp(self):
        FakePostgresRegistry.instances = []

    def postgres_module(self):
        module = types.ModuleType("core.postgres_workload_key_registry")
        module.PostgresWorkloadKeyRegistry = FakePostgresRegistry
        return module

    def test_sqlite_backend_is_selected_explicitly_and_closes_cleanly(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = create_workload_key_registry(
                {"storage_backend": "sqlite", "workspace": directory},
                authorize_admin=lambda identity: identity == "security-admin",
            )
            self.assertEqual(
                registry.database_path,
                str(Path(directory) / "security-workload-keys.sqlite3"),
            )
            close_workload_key_registry(registry)

    def test_postgres_backend_uses_configured_pool_and_readiness_check(self):
        authorizer = lambda identity: identity == "security-admin"
        with patch.dict(sys.modules, {
            "core.postgres_workload_key_registry": self.postgres_module(),
        }):
            registry = create_workload_key_registry(
                {
                    "storage_backend": "postgres",
                    "database_url": "postgresql://user:pass@localhost/yuki",
                    "database_pool_min": 2,
                    "database_pool_max": 7,
                },
                authorize_admin=authorizer,
            )
        self.assertIs(registry, FakePostgresRegistry.instances[-1])
        self.assertEqual(registry.min_pool_size, 2)
        self.assertEqual(registry.max_pool_size, 7)
        self.assertIs(registry.authorize_admin, authorizer)
        close_workload_key_registry(registry)
        self.assertTrue(registry.closed)

    def test_postgres_missing_url_fails_without_sqlite_fallback(self):
        with self.assertRaisesRegex(ValueError, "database_url_required"):
            create_workload_key_registry(
                {"storage_backend": "postgres", "workspace": "/tmp"},
                authorize_admin=lambda _: True,
            )
        self.assertEqual(FakePostgresRegistry.instances, [])

    def test_postgres_schema_failure_closes_pool_and_fails_startup(self):
        class SchemaFailure(FakePostgresRegistry):
            def check_ready(self):
                raise RuntimeError("migration_missing")

        module = types.ModuleType("core.postgres_workload_key_registry")
        module.PostgresWorkloadKeyRegistry = SchemaFailure
        with patch.dict(sys.modules, {"core.postgres_workload_key_registry": module}):
            with self.assertRaisesRegex(RuntimeError, "unavailable or not migrated"):
                create_workload_key_registry(
                    {
                        "storage_backend": "postgres",
                        "database_url": "postgresql://user:pass@localhost/yuki",
                    },
                    authorize_admin=lambda _: True,
                )
        self.assertTrue(SchemaFailure.instances[-1].closed)

    def test_rejects_unknown_backend_and_invalid_dependencies(self):
        with self.assertRaisesRegex(ValueError, "storage_backend"):
            create_workload_key_registry(
                {"storage_backend": "mysql"},
                authorize_admin=lambda _: True,
            )
        with self.assertRaisesRegex(TypeError, "admin_authorizer_required"):
            create_workload_key_registry(
                {"storage_backend": "sqlite", "workspace": "/tmp"},
                authorize_admin=None,
            )

    def test_postgres_pool_bounds_are_validated_before_construction(self):
        with patch.dict(sys.modules, {
            "core.postgres_workload_key_registry": self.postgres_module(),
        }):
            with self.assertRaisesRegex(ValueError, "pool_configuration_invalid"):
                create_workload_key_registry(
                    {
                        "storage_backend": "postgres",
                        "database_url": "postgresql://user:pass@localhost/yuki",
                        "database_pool_min": 8,
                        "database_pool_max": 2,
                    },
                    authorize_admin=lambda _: True,
                )
        self.assertEqual(FakePostgresRegistry.instances, [])


if __name__ == "__main__":
    unittest.main()
