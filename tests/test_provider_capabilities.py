import unittest

from providers.capabilities import (
    CapabilityRegistry, ProviderCapabilities, registry_from_catalog,
)
from yuki_providers import ProviderManager


def provider(name, *, caps=("text",), implemented=True, ready=True, privacy="local"):
    return ProviderCapabilities(name, frozenset(caps), implemented, ready, privacy)


class CapabilityRegistryTests(unittest.TestCase):
    def test_selects_preferred_ready_local_provider(self):
        registry = CapabilityRegistry([
            provider("ollama", caps=("text", "local_inference")),
            provider("gemini", caps=("text", "official_cli"), privacy="cloud_provider"),
            provider("mock", caps=("text",)),
        ])
        selected = registry.route(required=("text",), preference=("ollama", "mock"))
        self.assertEqual(selected.provider, "ollama")

    def test_cloud_is_excluded_unless_explicitly_allowed(self):
        registry = CapabilityRegistry([
            provider("gemini", privacy="cloud_provider"),
            provider("mock"),
        ])
        self.assertEqual(registry.route().provider, "mock")
        self.assertEqual(registry.route(allow_cloud=True, preference=("gemini",)).provider, "gemini")

    def test_unknown_privacy_never_routes_even_with_cloud_opt_in(self):
        registry = CapabilityRegistry([
            provider("unknown", privacy="unknown"),
            provider("gateway", privacy="third_party_gateway"),
        ])
        with self.assertRaises(LookupError):
            registry.route(allow_cloud=True, preference=("unknown",))
        self.assertEqual(
            registry.route(allow_cloud=True, preference=("gateway",)).provider,
            "gateway",
        )

    def test_route_rejects_string_iterables_and_invalid_cloud_flags(self):
        registry = CapabilityRegistry([provider("mock")])
        for kwargs in (
            {"required": "text"},
            {"preference": "mock"},
            {"allow_cloud": 1},
            {"required": ("text", "local_inference", "official_cli", "model_discovery", "per_task_routing", "multi_provider_routing", "text")},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises((TypeError, ValueError)):
                registry.route(**kwargs)

    def test_route_bounds_generator_consumption(self):
        consumed = []
        def required():
            for index in range(100):
                consumed.append(index)
                yield "text"
        with self.assertRaises(ValueError):
            CapabilityRegistry([provider("mock")]).route(required=required())
        self.assertEqual(consumed, list(range(7)))

    def test_unrecognized_privacy_classification_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "recognized classification"):
            provider("unsafe", privacy="anything")

    def test_requires_all_capabilities(self):
        registry = CapabilityRegistry([provider("mock"), provider("ollama", caps=("text", "local_inference"))])
        self.assertEqual(registry.route(required=("text", "local_inference")).provider, "ollama")
        with self.assertRaises(LookupError):
            registry.route(required=("text", "official_cli"))

    def test_unimplemented_or_unready_provider_never_selected(self):
        registry = CapabilityRegistry([
            provider("unimplemented", implemented=False, ready=False),
            provider("unready", ready=False),
        ])
        with self.assertRaises(LookupError):
            registry.route()

    def test_unknown_capabilities_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "unknown required capabilities"):
            CapabilityRegistry([provider("mock")]).route(required=("text", "magic"))
        with self.assertRaisesRegex(ValueError, "unknown provider capabilities"):
            provider("bad", caps=("text", "magic"))

    def test_rejects_duplicate_and_non_normalized_providers(self):
        with self.assertRaisesRegex(ValueError, "normalized"):
            ProviderCapabilities("Mock", frozenset({"text"}), True, True, "local")
        registry = CapabilityRegistry([provider("mock")])
        with self.assertRaisesRegex(ValueError, "already registered"):
            registry.register(provider("mock"))
        with self.assertRaises(TypeError):
            registry.register({"provider": "fake"})

    def test_catalog_conversion_keeps_unimplemented_cloud_not_ready(self):
        catalog = [
            {"name": "mock", "capabilities": ["text"], "implemented": True, "ready": True, "privacy": "local"},
            {"name": "openai", "capabilities": ["text"], "implemented": False, "ready": False, "privacy": "cloud_provider"},
        ]
        registry = registry_from_catalog(catalog)
        self.assertEqual(registry.route().provider, "mock")
        self.assertEqual(registry.route(allow_cloud=True, preference=("openai",)).provider, "mock")

    def test_list_is_sanitized_and_sorted(self):
        registry = CapabilityRegistry([provider("ollama", caps=("text", "local_inference")), provider("mock")])
        items = registry.list()
        self.assertEqual([item["provider"] for item in items], ["mock", "ollama"])
        self.assertNotIn("secret", repr(items))

    def test_provider_manager_exposes_registry_from_real_catalog(self):
        manager = ProviderManager(environ={})
        registry = manager.routing_registry()
        self.assertEqual(registry.route(required=("text",)).provider, "mock")
        self.assertIsNone(registry.get("openai").configured_model)
        self.assertFalse(registry.get("openai").implemented)


if __name__ == "__main__":
    unittest.main()
