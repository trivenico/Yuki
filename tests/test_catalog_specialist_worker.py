import unittest
from unittest.mock import MagicMock

from automation.catalog_specialist_worker import create_catalog_specialist_worker
from automation.specialist_worker import SpecialistTaskWorker
from automation.task_runtime import TaskRuntime


class FakeProvider:
    def is_available(self):
        return True
    def generate(self, prompt):
        return "specialist output"


class FakeProviderManager:
    def create_selected_adapter(self):
        return FakeProvider()


class CatalogSpecialistWorkerFactoryTests(unittest.TestCase):
    def test_builds_existing_worker_with_catalog_ids(self):
        runtime = MagicMock(spec=TaskRuntime)
        worker = create_catalog_specialist_worker(
            runtime,
            worker_id="catalog-worker",
            service_actor_id="worker-service",
            provider_manager=FakeProviderManager(),
            role_map={"research": "research", "engineering": "coding"},
        )
        self.assertIsInstance(worker, SpecialistTaskWorker)
        self.assertEqual(set(worker.handlers), {"research", "engineering"})
        self.assertEqual(worker.handlers["research"]("Investigate"), "specialist output")

    def test_invalid_runtime_rejected(self):
        with self.assertRaises(TypeError):
            create_catalog_specialist_worker(
                object(), worker_id="w", service_actor_id="svc",
                provider_manager=FakeProviderManager(), role_map={"research": "research"},
            )

    def test_provider_preferences_and_cloud_policy_are_forwarded(self):
        class RoutedManager(FakeProviderManager):
            def __init__(self):
                self.calls = []
            def create_routed_adapter(self, **kwargs):
                self.calls.append(kwargs)
                return FakeProvider()

        manager = RoutedManager()
        create_catalog_specialist_worker(
            MagicMock(spec=TaskRuntime),
            worker_id="catalog-worker", service_actor_id="worker-service",
            provider_manager=manager, role_map={"research": "research"},
            provider_preferences={"research": ("ollama",)}, allow_cloud=False,
        )
        self.assertEqual(manager.calls[0]["preference"], ("ollama",))
        self.assertFalse(manager.calls[0]["allow_cloud"])


if __name__ == "__main__":
    unittest.main()
