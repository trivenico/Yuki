import unittest
from providers.catalog import get_provider, implemented_provider_ids, list_providers
from providers.task_router import TaskRouter

class StubProvider:
    def __init__(self, value, available=True, error=None):
        self.value, self.available, self.error, self.calls = value, available, error, []
    def is_available(self): return self.available
    def generate(self, prompt):
        self.calls.append(prompt)
        if self.error: raise self.error
        return self.value

class TaskRouterTests(unittest.TestCase):
    def setUp(self):
        self.local, self.cloud = StubProvider("local"), StubProvider("cloud")
        self.router = TaskRouter({"ollama":self.local,"cloud":self.cloud}, default_provider="ollama", task_routes={"deep_research":"cloud"})
    def test_default_and_unlisted_task_use_local(self):
        self.assertEqual(self.router.generate_for("general", "hi"), "local")
        self.assertEqual(self.router.generate("hello"), "local")
        self.assertEqual(self.local.calls, ["hi", "hello"])
        self.assertEqual(self.cloud.calls, [])
    def test_explicit_task_uses_configured_provider(self):
        self.assertEqual(self.router.generate_for("deep_research", "research"), "cloud")
    def test_unavailable_route_fails_without_fallback(self):
        self.cloud.available = False
        with self.assertRaisesRegex(RuntimeError, "implicit fallback is disabled"):
            self.router.generate_for("deep_research", "private")
        self.assertEqual(self.local.calls, [])
    def test_provider_error_is_not_retried(self):
        self.cloud.error = RuntimeError("upstream failed")
        with self.assertRaisesRegex(RuntimeError, "upstream failed"):
            self.router.generate_for("deep_research", "research")
        self.assertEqual(self.local.calls, [])
    def test_invalid_routes_fail_closed(self):
        with self.assertRaises(ValueError): TaskRouter({"ollama":self.local}, default_provider="missing")
        with self.assertRaises(ValueError): TaskRouter({"ollama":self.local}, default_provider="ollama", task_routes={"research":"missing"})
        with self.assertRaises(ValueError): self.router.provider_for("../cloud")
    def test_catalog_distinguishes_implemented_from_planned(self):
        self.assertIn("ollama", implemented_provider_ids())
        self.assertNotIn("openrouter", implemented_provider_ids())
        self.assertFalse(get_provider("openrouter")["implemented"])
        self.assertTrue(get_provider("ollama")["implemented"])
        self.assertGreaterEqual(len(list_providers()), 10)
    def test_unknown_catalog_entry_fails_closed(self):
        with self.assertRaises(ValueError): get_provider("made_up_provider")

if __name__ == "__main__": unittest.main()
