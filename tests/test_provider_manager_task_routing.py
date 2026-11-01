import unittest

from providers.task_routing import TaskProfile
from yuki_providers import ProviderManager


class ProviderManagerTaskRoutingTests(unittest.TestCase):
    def test_task_route_requires_explicit_cloud_consent_and_applies_effort(self):
        manager = ProviderManager(environ={
            "OPENAI_API_KEY": "test-key",
            "YUKI_OPENAI_RESPONSES_MODEL": "gpt-test",
        })
        with self.assertRaises(LookupError):
            manager.create_task_routed_adapter(TaskProfile("complex_reasoning"))
        decision, adapter = manager.create_task_routed_adapter(TaskProfile(
            "complex_reasoning", privacy="cloud_allowed",
            preferred_providers=("openai_responses",),
        ))
        self.assertEqual(decision.provider, "openai_responses")
        self.assertEqual(decision.reasoning_effort, "xhigh")
        self.assertEqual(adapter.reasoning_effort, "xhigh")

    def test_security_route_marks_human_review_required(self):
        manager = ProviderManager(environ={
            "ANTHROPIC_API_KEY": "test-key",
            "YUKI_ANTHROPIC_MODEL": "claude-test",
        })
        decision, adapter = manager.create_task_routed_adapter(TaskProfile(
            "security", privacy="cloud_allowed",
            preferred_providers=("anthropic",),
        ))
        self.assertEqual(decision.provider, "anthropic")
        self.assertTrue(decision.require_human_review)
        self.assertEqual(adapter.effort, "high")

    def test_unavailable_selected_adapter_fails_without_fallback(self):
        manager = ProviderManager(environ={
            "OPENAI_API_KEY": "test-key",
            "YUKI_OPENAI_RESPONSES_MODEL": "gpt-test",
        })
        manager._build_adapter = lambda _name: type("Unavailable", (), {
            "is_available": lambda self: False,
            "generate": lambda self, prompt: "",
        })()
        with self.assertRaisesRegex(RuntimeError, "no fallback"):
            manager.create_task_routed_adapter(TaskProfile(
                "engineering", privacy="cloud_allowed",
                preferred_providers=("openai_responses",),
            ))


if __name__ == "__main__":
    unittest.main()
