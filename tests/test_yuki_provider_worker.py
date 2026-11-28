import tempfile
import unittest
from contextlib import contextmanager
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
        self.active_leases = 0
        self.closing = False

    def create_selected_adapter(self):
        return self.provider

    @contextmanager
    def lease_adapter(self, adapter):
        if self.closing:
            raise RuntimeError("provider manager is closing or closed.")
        if adapter is not self.provider:
            raise ValueError("adapter is not owned by this manager")
        self.active_leases += 1
        try:
            yield adapter
        finally:
            self.active_leases -= 1

    def close(self):
        self.closing = True
        if self.active_leases:
            raise RuntimeError("active adapter lease")


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

    def test_provider_request_holds_lease_until_generation_finishes(self):
        class ClosingProvider(FakeProvider):
            def __init__(self, manager):
                super().__init__()
                self.manager = manager
                self.close_blocked = False

            def generate(self, prompt):
                with self.assert_manager_lease():
                    return super().generate(prompt)

            @contextmanager
            def assert_manager_lease(self):
                try:
                    self.manager.close()
                except RuntimeError as exc:
                    self.close_blocked = "active adapter lease" in str(exc)
                else:
                    raise AssertionError("provider manager closed during generation")
                yield

        provider = ClosingProvider(None)
        manager = FakeManager(provider)
        provider.manager = manager
        handlers = create_provider_handlers(manager)

        class Context:
            @staticmethod
            def check_cancelled():
                return None

        result = handlers["model.generate"]({"prompt": "leased"}, Context())
        self.assertEqual(result["text"], "answer")
        self.assertTrue(provider.close_blocked)
        self.assertEqual(manager.active_leases, 0)

    def test_manager_without_request_lease_is_rejected(self):
        class LegacyManager:
            def create_selected_adapter(self):
                return FakeProvider()

        with self.assertRaisesRegex(TypeError, "lease_adapter"):
            create_provider_handlers(LegacyManager())

    def test_invalid_manager_and_prompt_limit_are_rejected(self):
        with self.assertRaises(TypeError):
            create_provider_handlers(object())
        with self.assertRaises(ValueError):
            create_provider_handlers(FakeManager(FakeProvider()), max_prompt_chars=0)


if __name__ == "__main__":
    unittest.main()
