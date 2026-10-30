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

    def test_worker_executes_task_written_to_shared_gateway_database(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "gateway.sqlite3")
            service = build_worker_service(
                database_path=database,
                provider_manager=FakeProviderManager(),
            )
            queued = service.runtime.store.create(
                idempotency_key="gateway-task-1",
                workflow_name="agent-research-analyst-request",
                definition=[{"name": "specialist-output", "action": "emit",
                             "argument": "Objective: summarize supplied evidence",
                             "requires_approval": False}],
            )
            result = service.worker.run_once()
            self.assertIsNotNone(result)
            self.assertEqual(result.task_id, queued.run_id)
            self.assertEqual(result.state, "succeeded")
            self.assertIn("completed:", result.result["output"])

    def test_cloud_provider_requires_explicit_consent_for_each_registered_agent(self):
        import os
        from unittest.mock import patch
        from automation.default_agents import starter_agent_specs

        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "gateway.sqlite3")
            cloud_manager = FakeProviderManager()
            cloud_manager.current = lambda: SimpleNamespace(name="oci")
            cloud_manager.create_routed_adapter = lambda **kwargs: (
                FakeProvider() if kwargs["allow_cloud"] else
                (_ for _ in ()).throw(RuntimeError("cloud route denied"))
            )
            with patch.dict(os.environ, {"YUKI_WORKER_CLOUD_CONSENT_AGENT_IDS": "research-analyst"}):
                with self.assertRaisesRegex(RuntimeError, "cloud route denied"):
                    build_worker_service(database_path=database, provider_manager=cloud_manager)
            consent = ",".join(spec.agent_id for spec in starter_agent_specs())
            with patch.dict(os.environ, {"YUKI_WORKER_CLOUD_CONSENT_AGENT_IDS": consent}):
                service = build_worker_service(database_path=database, provider_manager=cloud_manager)
            self.assertEqual(len(service.worker.handlers), 21)

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
