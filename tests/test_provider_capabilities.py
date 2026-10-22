import unittest

from providers.capabilities import (
    CapabilityRegistry, ProviderCapabilities, registry_from_catalog,
)


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
        with self.assertRaises(LookupError):
            registry.route(allow_cloud=True, preference=("openai",))

    def test_list_is_sanitized_and_sorted(self):
        registry = CapabilityRegistry([provider("ollama", caps=("text", "local_inference")), provider("mock")])
        items = registry.list()
        self.assertEqual([item["provider"] for item in items], ["mock", "ollama"])
        self.assertNotIn("secret", repr(items))


if __name__ == "__main__":
    unittest.main()
