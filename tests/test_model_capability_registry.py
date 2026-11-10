import unittest

from providers.model_registry import ModelCapabilityRegistry, ModelProfile


def model(provider, model_id, *, caps=("text",), privacy="local",
          implemented=True, ready=True, context=8192, evidence="owner_verified"):
    return ModelProfile(
        provider, model_id, frozenset(caps), implemented, ready,
        privacy, context, evidence,
    )


class ModelCapabilityRegistryTests(unittest.TestCase):
    def test_routes_exact_model_capability_not_vendor_name(self):
        registry = ModelCapabilityRegistry([
            model("openai", "text-only", privacy="cloud_provider"),
            model("openai", "vision-model", caps=("text", "vision_input"),
                  privacy="cloud_provider"),
        ])
        selected = registry.route(
            required=("text", "vision_input"), allow_cloud=True,
        )
        self.assertEqual(selected.model_id, "vision-model")

    def test_cloud_models_require_explicit_opt_in(self):
        registry = ModelCapabilityRegistry([
            model("openai", "frontier", privacy="cloud_provider"),
            model("ollama", "local-qwen"),
        ])
        self.assertEqual(registry.route().provider, "ollama")
        with self.assertRaises(LookupError):
            registry.route(provider_preference=("openai",), strict_provider_preference=True)
        self.assertEqual(
            registry.route(allow_cloud=True, provider_preference=("openai",),
                           strict_provider_preference=True).provider,
            "openai",
        )

    def test_exact_model_preference_precedes_provider_preference(self):
        registry = ModelCapabilityRegistry([
            model("ollama", "local-small"),
            model("openai", "cloud-large", privacy="cloud_provider"),
        ])
        selected = registry.route(
            allow_cloud=True, model_preference=("cloud-large",),
            provider_preference=("ollama", "openai"),
        )
        self.assertEqual(selected.model_id, "cloud-large")

    def test_unknown_capabilities_are_rejected_not_guessed(self):
        with self.assertRaisesRegex(ValueError, "unknown model capabilities"):
            model("vendor", "unknown", caps=("text", "quantum"))
        with self.assertRaisesRegex(ValueError, "unknown required capabilities"):
            ModelCapabilityRegistry([model("local", "m")]).route(required=("magic",))

    def test_unimplemented_or_unready_models_never_route(self):
        registry = ModelCapabilityRegistry([
            model("vendor", "planned", implemented=False, ready=False),
            model("vendor", "offline", ready=False),
        ])
        with self.assertRaises(LookupError):
            registry.route()

    def test_context_window_is_a_hard_requirement(self):
        registry = ModelCapabilityRegistry([
            model("local", "short", context=4096),
            model("local", "long", context=131072),
        ])
        self.assertEqual(registry.route(min_context_window=100000).model_id, "long")
        with self.assertRaises(LookupError):
            registry.route(min_context_window=1_000_000)

    def test_unknown_context_is_not_treated_as_unbounded(self):
        registry = ModelCapabilityRegistry([
            model("local", "unknown-context", context=None),
        ])
        with self.assertRaises(LookupError):
            registry.route(min_context_window=1)

    def test_duplicate_models_and_invalid_metadata_are_rejected(self):
        registry = ModelCapabilityRegistry([model("ollama", "qwen")])
        with self.assertRaisesRegex(ValueError, "already registered"):
            registry.register(model("ollama", "qwen"))
        with self.assertRaisesRegex(ValueError, "recognized model privacy"):
            model("bad", "model", privacy="unknown")
        with self.assertRaisesRegex(ValueError, "evidence_source"):
            model("bad", "model", evidence="guessed")

    def test_bounds_preference_consumption(self):
        consumed = []
        def many():
            for i in range(100):
                consumed.append(i)
                yield "text"
        registry = ModelCapabilityRegistry([model("local", "m")])
        with self.assertRaises(ValueError):
            registry.route(required=many())
        self.assertEqual(consumed, list(range(11)))

    def test_rejects_string_iterables_and_invalid_flags(self):
        registry = ModelCapabilityRegistry([model("local", "m")])
        for kwargs in (
            {"required": "text"},
            {"provider_preference": "local"},
            {"model_preference": "m"},
            {"allow_cloud": 1},
            {"strict_provider_preference": 1},
            {"min_context_window": True},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises((TypeError, ValueError)):
                registry.route(**kwargs)

    def test_list_exposes_metadata_without_credentials(self):
        registry = ModelCapabilityRegistry([model("local", "m", caps=("text", "coding"))])
        rows = registry.list()
        self.assertEqual(rows[0]["capabilities"], ["coding", "text"])
        self.assertNotIn("api_key", repr(rows).lower())


if __name__ == "__main__":
    unittest.main()
