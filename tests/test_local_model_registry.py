"""Tests for safe read-only local model inventory and recommendations."""
import json
import unittest

from core.local_model_registry import (
    LocalModel,
    LocalModelRegistry,
    ModelRegistryError,
)


class _Response:
    status = 200

    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        return self.payload[:size]


class LocalModelRegistryTests(unittest.TestCase):
    def test_discovery_parses_and_sorts_local_models(self):
        registry = LocalModelRegistry(opener=lambda request, timeout: _Response({
            "models": [
                {"name": "zeta:latest", "size": 123, "digest": "sha-z",
                 "details": {"family": "llama", "parameter_size": "8B",
                             "quantization_level": "Q4_K_M"}},
                {"name": "alpha:latest", "size": 456,
                 "details": {"family": "qwen", "parameter_size": "7B",
                             "quantization_level": "Q5_K_M"}},
            ]
        }))
        models = registry.discover()
        self.assertEqual([model.name for model in models], ["alpha:latest", "zeta:latest"])
        self.assertEqual(models[1].parameter_size, "8B")
        self.assertEqual(models[1].license_status, "unknown")

    def test_rejects_non_loopback_endpoint(self):
        with self.assertRaises(ValueError):
            LocalModelRegistry("https://example.com")

    def test_rejects_invalid_inventory(self):
        registry = LocalModelRegistry(opener=lambda request, timeout: _Response({"models": "bad"}))
        with self.assertRaises(ModelRegistryError):
            registry.discover()

    def test_response_size_is_bounded(self):
        registry = LocalModelRegistry(
            opener=lambda request, timeout: _Response({"models": []}),
            max_response_bytes=2,
        )
        with self.assertRaises(ModelRegistryError):
            registry.discover()

    def test_memory_recommendations_are_heuristic_and_conservative(self):
        models = [
            LocalModel("small:latest", None, None, None, "llama", "3B", "Q4_K_M"),
            LocalModel("large:latest", None, None, None, "llama", "70B", "Q4_K_M"),
            LocalModel("unknown:latest", None, None, None, None, None, None),
        ]
        recommendations = LocalModelRegistry.recommend(models, available_memory_gib=8)
        by_name = {item.model_name: item for item in recommendations}
        self.assertEqual(by_name["small:latest"].status, "candidate")
        self.assertEqual(by_name["large:latest"].status, "unlikely_to_fit")
        self.assertEqual(by_name["unknown:latest"].status, "unknown")
        self.assertTrue(all(item.role_fit == "unverified" for item in recommendations))

    def test_missing_available_memory_never_claims_fit(self):
        model = LocalModel("small", None, None, None, None, "3B", "Q4")
        recommendation = LocalModelRegistry.recommend(
            [model], available_memory_gib=None
        )[0]
        self.assertEqual(recommendation.status, "unknown")


if __name__ == "__main__":
    unittest.main()
