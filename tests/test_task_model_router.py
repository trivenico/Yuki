import unittest
from unittest.mock import Mock

from providers.capabilities import CapabilityRegistry, ProviderCapabilities
from providers.task_router import TaskModelRouter


def entry(name, privacy="local_runtime", ready=True, model="local-model"):
    return ProviderCapabilities(
        name, frozenset({"text"}), True, ready, privacy, model
    )


class TaskModelRouterTests(unittest.TestCase):
    def setUp(self):
        self.registry = CapabilityRegistry([
            entry("ollama"),
            entry("openai", "cloud_provider", model="cloud-model"),
            entry("gemini", "cloud_provider", model=None),
        ])
        self.manager = Mock()
        self.manager.routing_registry.return_value = self.registry
        self.manager.create_routed_adapter.side_effect = lambda **kwargs: (
            "adapter:" + kwargs["preference"][0]
        )
        self.router = TaskModelRouter(self.manager)

    def test_every_profile_defaults_to_local_provider(self):
        for task in ("conversation", "coding", "research", "planning", "analysis"):
            with self.subTest(task=task):
                result = self.router.route(task)
                self.assertEqual(result.provider, "ollama")
                self.assertEqual(result.privacy, "local_runtime")
                self.assertEqual(result.adapter, "adapter:ollama")

    def test_cloud_is_never_selected_without_explicit_opt_in(self):
        with self.assertRaises(LookupError):
            self.router.route("research", preference=("openai",))
        self.manager.create_routed_adapter.assert_not_called()

    def test_cloud_can_be_selected_only_with_explicit_opt_in(self):
        result = self.router.route(
            "analysis", preference=("openai",), allow_cloud=True
        )
        self.assertEqual(result.provider, "openai")
        self.assertEqual(result.privacy, "cloud_provider")
        self.manager.create_routed_adapter.assert_called_once_with(
            required=("text",), preference=("openai",), allow_cloud=True
        )

    def test_strict_preference_never_substitutes_another_provider(self):
        with self.assertRaises(LookupError):
            self.router.route("coding", preference=("gemini",))
        self.manager.create_routed_adapter.assert_not_called()

    def test_unready_provider_is_not_selected(self):
        registry = CapabilityRegistry([entry("ollama", ready=False)])
        self.manager.routing_registry.return_value = registry
        with self.assertRaises(LookupError):
            self.router.route("conversation")

    def test_invalid_task_and_cloud_flag_are_rejected(self):
        with self.assertRaises(ValueError):
            self.router.route("image-generation")
        with self.assertRaises(TypeError):
            self.router.route("conversation", allow_cloud=1)

    def test_invalid_preference_is_rejected(self):
        for preference in ("ollama", [], [" "], ["ollama"] * 17):
            with self.subTest(preference=preference), self.assertRaises((TypeError, ValueError)):
                self.router.route("coding", preference=preference)

    def test_profiles_are_descriptive_and_sorted(self):
        profiles = self.router.profiles()
        self.assertEqual(
            [profile.task for profile in profiles],
            ["analysis", "coding", "conversation", "planning", "research"],
        )
        self.assertTrue(all(profile.required_capabilities == ("text",) for profile in profiles))


if __name__ == "__main__":
    unittest.main()
