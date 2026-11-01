import unittest

from providers.capabilities import CapabilityRegistry, ProviderCapabilities
from providers.task_routing import TaskProfile, TaskRoutingPolicy, route_task


def entry(name, *, privacy="local_runtime", model=None, ready=True, caps=("text",)):
    return ProviderCapabilities(
        name, frozenset(caps), True, ready, privacy, configured_model=model
    )


class TaskRoutingTests(unittest.TestCase):
    def setUp(self):
        self.registry = CapabilityRegistry([
            entry("ollama", model="qwen-local"),
            entry("openai_responses", privacy="cloud_provider", model="gpt-reasoner"),
            entry("anthropic", privacy="cloud_provider", model="claude-model"),
        ])

    def test_privacy_defaults_to_local_even_for_complex_tasks(self):
        decision = route_task(self.registry, TaskProfile("complex_reasoning"))
        self.assertEqual(decision.provider, "ollama")
        self.assertEqual(decision.privacy, "local_only")
        self.assertEqual(decision.reasoning_effort, "xhigh")

    def test_cloud_requires_explicit_per_task_opt_in(self):
        profile = TaskProfile(
            "engineering", privacy="cloud_allowed",
            preferred_providers=("openai_responses", "anthropic"),
        )
        decision = TaskRoutingPolicy().route(self.registry, profile)
        self.assertEqual(decision.provider, "openai_responses")
        self.assertEqual(decision.reasoning_effort, "high")

    def test_security_always_requires_human_review(self):
        decision = route_task(self.registry, TaskProfile("security"))
        self.assertTrue(decision.require_human_review)
        self.assertEqual(decision.provider, "ollama")

    def test_explicit_review_flag_applies_to_other_task_types(self):
        decision = route_task(
            self.registry, TaskProfile("routine", require_human_review=True)
        )
        self.assertTrue(decision.require_human_review)

    def test_unready_local_provider_does_not_trigger_cloud_fallback(self):
        registry = CapabilityRegistry([
            entry("ollama", ready=False),
            entry("openai_responses", privacy="cloud_provider", model="gpt-reasoner"),
        ])
        with self.assertRaises(LookupError):
            route_task(registry, TaskProfile("routine"))

    def test_strict_privacy_excludes_cloud_even_if_preferred(self):
        profile = TaskProfile(
            "routine", preferred_providers=("openai_responses", "ollama")
        )
        self.assertEqual(route_task(self.registry, profile).provider, "ollama")

    def test_unknown_or_invalid_profiles_fail_closed(self):
        for kwargs in (
            {"task_type": "magic"},
            {"task_type": "security", "privacy": "guess"},
            {"task_type": "routine", "reasoning_effort": "turbo"},
            {"task_type": "routine", "require_human_review": 1},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises((TypeError, ValueError)):
                TaskProfile(**kwargs)

    def test_decision_is_explainable_and_does_not_claim_cost_or_quality(self):
        decision = route_task(self.registry, TaskProfile("routine"))
        self.assertIn("cloud excluded", decision.rationale)
        self.assertNotIn("cost", decision.rationale.lower())
        self.assertNotIn("best model", decision.rationale.lower())


if __name__ == "__main__":
    unittest.main()
