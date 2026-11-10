
import unittest

from providers.model_registry import ModelCapabilityProfile, ModelCapabilityRegistry


def profile(provider, model, caps=("text",), privacy="local", ready=True,
            implemented=True, context=8192, evidence="owner_verified"):
    return ModelCapabilityProfile(provider, model, frozenset(caps), implemented,
                                  ready, privacy, context, evidence)


class ModelCapabilityRegistryTests(unittest.TestCase):
    def test_routes_exact_model_capabilities(self):
        registry = ModelCapabilityRegistry([
            profile("openai", "openai/text-only", privacy="cloud_provider"),
            profile("openai", "openai/vision", ("text", "vision_input"), "cloud_provider"),
        ])
        self.assertEqual(registry.route(required=("text", "vision_input"),
                                        allow_cloud=True).model_id, "openai/vision")

    def test_cloud_requires_explicit_opt_in(self):
        registry = ModelCapabilityRegistry([
            profile("openai", "openai/frontier", privacy="cloud_provider"),
            profile("ollama", "ollama/local"),
        ])
        self.assertEqual(registry.route().provider, "ollama")
        with self.assertRaises(LookupError):
            registry.route(provider_preference=("openai",), strict_provider_preference=True)
        self.assertEqual(registry.route(allow_cloud=True, provider_preference=("openai",),
                                        strict_provider_preference=True).provider, "openai")

    def test_exact_model_preference_precedes_provider_preference(self):
        registry = ModelCapabilityRegistry([
            profile("ollama", "ollama/small"),
            profile("openai", "openai/large", privacy="cloud_provider"),
        ])
        self.assertEqual(registry.route(allow_cloud=True, model_preference=("openai/large",),
                                        provider_preference=("ollama", "openai")).model_id,
                         "openai/large")

    def test_unknown_capabilities_are_never_guessed(self):
        with self.assertRaisesRegex(ValueError, "unknown model capabilities"):
            profile("vendor", "vendor/model", ("text", "magic"))
        with self.assertRaisesRegex(ValueError, "unknown required capabilities"):
            ModelCapabilityRegistry([profile("local", "local/model")]).route(required=("magic",))

    def test_unready_or_unimplemented_models_never_route(self):
        registry = ModelCapabilityRegistry([
            profile("vendor", "vendor/planned", ready=False, implemented=False),
            profile("vendor", "vendor/offline", ready=False),
        ])
        with self.assertRaises(LookupError):
            registry.route()

    def test_context_window_is_enforced(self):
        registry = ModelCapabilityRegistry([
            profile("local", "local/short", context=4096),
            profile("local", "local/long", context=131072),
            profile("local", "local/unknown", context=None),
        ])
        self.assertEqual(registry.route(min_context_window=100000).model_id, "local/long")
        with self.assertRaises(LookupError):
            registry.route(min_context_window=1_000_000)

    def test_invalid_duplicate_and_unverified_metadata_rejected(self):
        registry = ModelCapabilityRegistry([profile("ollama", "ollama/qwen")])
        with self.assertRaisesRegex(ValueError, "already registered"):
            registry.register(profile("ollama", "ollama/qwen"))
        with self.assertRaisesRegex(ValueError, "privacy"):
            profile("bad", "bad/model", privacy="unknown")
        with self.assertRaisesRegex(ValueError, "evidence_source"):
            profile("bad", "bad/model", evidence="guessed")

    def test_input_bounds_and_types(self):
        registry = ModelCapabilityRegistry([profile("local", "local/model")])
        consumed = []
        def many():
            for i in range(100):
                consumed.append(i)
                yield "text"
        with self.assertRaises(ValueError):
            registry.route(required=many())
        self.assertEqual(consumed, list(range(11)))
        for kwargs in ({"required": "text"}, {"provider_preference": "local"},
                       {"model_preference": "local/model"}, {"allow_cloud": 1},
                       {"strict_provider_preference": 1}, {"min_context_window": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises((TypeError, ValueError)):
                registry.route(**kwargs)

    def test_listing_is_sanitized(self):
        rows = ModelCapabilityRegistry([
            profile("local", "local/model", ("text", "coding"))
        ]).list()
        self.assertEqual(rows[0]["capabilities"], ["coding", "text"])
        self.assertNotIn("api_key", repr(rows).lower())


if __name__ == "__main__":
    unittest.main()
