import tempfile
import unittest
from pathlib import Path

from yuki_agent_runtime import TaskStore
from yuki_agent_worker import AgentWorker
from yuki_provider_worker import create_provider_handlers


class FakeProvider:
    def __init__(self, answer="answer"):
        self.answer = answer
        self.prompts = []

    def is_available(self):
        return True

    def generate(self, prompt):
        self.prompts.append(prompt)
        return self.answer


class FakeManager:
    def __init__(self, provider):
        self.provider = provider

    def create_selected_adapter(self):
        return self.provider


class ProviderWorkerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = TaskStore(Path(self.temp.name) / "tasks.sqlite3")

    def tearDown(self):
        self.temp.cleanup()

    def test_provider_handler_executes_through_durable_worker(self):
        provider = FakeProvider("Hello from the configured provider")
        handlers = create_provider_handlers(FakeManager(provider))
        task = self.store.submit("model.generate", {"prompt": "Say hello"}, approval_required=False)
        worker = AgentWorker(self.store, worker_id="provider-worker", handlers=handlers)
        outcome = worker.run_once()
        self.assertEqual(outcome.status, "succeeded")
        self.assertEqual(provider.prompts, ["Say hello"])
        self.assertEqual(self.store.get(task["id"])["result"], {
            "provider": "FakeProvider",
            "text": "Hello from the configured provider",
            "truncated": False,
        })

    def test_oversized_provider_response_reports_truncation(self):
        provider = FakeProvider("x" * 50_001)
        handlers = create_provider_handlers(FakeManager(provider))
        task = self.store.submit("model.generate", {"prompt": "hello"}, approval_required=False)
        outcome = AgentWorker(self.store, worker_id="provider-worker", handlers=handlers).run_once()
        self.assertEqual(outcome.status, "succeeded")
        result = self.store.get(task["id"])["result"]
        self.assertTrue(result["truncated"])
        self.assertTrue(result["text"].endswith("[Response truncated by Yuki.]"))
        self.assertEqual(len(result["text"]), 50_000 + len("\\n[Response truncated by Yuki.]"))

    def test_task_cannot_override_provider_or_endpoint(self):
        provider = FakeProvider()
        handlers = create_provider_handlers(FakeManager(provider))
        task = self.store.submit(
            "model.generate",
            {"prompt": "hello", "base_url": "https://attacker.invalid"},
            approval_required=False,
        )
        outcome = AgentWorker(self.store, worker_id="provider-worker", handlers=handlers).run_once()
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(provider.prompts, [])
        self.assertEqual(self.store.get(task["id"])["error"], "Handler failed: ValueError")

    def test_invalid_prompt_is_rejected_without_calling_provider(self):
        provider = FakeProvider()
        handlers = create_provider_handlers(FakeManager(provider))
        self.store.submit("model.generate", {"prompt": " "}, approval_required=False)
        outcome = AgentWorker(self.store, worker_id="provider-worker", handlers=handlers).run_once()
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(provider.prompts, [])

    def test_prompt_limit_is_enforced(self):
        provider = FakeProvider()
        handlers = create_provider_handlers(FakeManager(provider), max_prompt_chars=10)
        self.store.submit("model.generate", {"prompt": "x" * 11}, approval_required=False)
        outcome = AgentWorker(self.store, worker_id="provider-worker", handlers=handlers).run_once()
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(provider.prompts, [])

    def test_unavailable_provider_fails_at_handler_construction(self):
        class Unavailable(FakeProvider):
            def is_available(self):
                return False
        with self.assertRaisesRegex(RuntimeError, "not available"):
            create_provider_handlers(FakeManager(Unavailable()))

    def test_invalid_manager_and_prompt_limit_are_rejected(self):
        with self.assertRaises(TypeError):
            create_provider_handlers(object())
        with self.assertRaises(ValueError):
            create_provider_handlers(FakeManager(FakeProvider()), max_prompt_chars=0)


if __name__ == "__main__":
    unittest.main()
