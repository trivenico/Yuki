import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from automation.worker_service import build_worker_service
from automation.workflow_store import WorkflowRunStore


class FakeProvider:
    def is_available(self):
        return True

    def generate(self, prompt):
        return "completed: " + prompt[:80]


class FakeProviderManager:
    def current(self):
        return SimpleNamespace(name="ollama")

    def create_routed_adapter(self, *, required, preference, allow_cloud):
        if required != ("text",) or preference != ("ollama",) or allow_cloud:
            raise AssertionError("worker changed the explicit local-only provider policy")
        return FakeProvider()


class WorkerServiceTests(unittest.TestCase):
    def test_worker_uses_gateway_database_and_seeds_registered_fleet(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "gateway.sqlite3")
            service = build_worker_service(
                database_path=database,
                provider_manager=FakeProviderManager(),
            )
            self.assertEqual(service.runtime.store.database_path, database)
            self.assertEqual(len(service.worker.handlers), 21)
            self.assertIsNotNone(service.supervisor)
            registry = service.runtime.store
            self.assertIsInstance(registry, WorkflowRunStore)
            self.assertEqual(registry.database_path, database)

    def test_worker_service_rejects_missing_provider_selection(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict("os.environ", {"YUKI_PROVIDER": ""}):
                with self.assertRaisesRegex(RuntimeError, "YUKI_PROVIDER"):
                    build_worker_service(
                        database_path=str(Path(directory) / "gateway.sqlite3"),
                    )


if __name__ == "__main__":
    unittest.main()
