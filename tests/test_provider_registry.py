"""Tests for trusted provider registration and capability-based resolution."""
import unittest

from providers.agent_adapter import ProviderAgentAdapter
from providers.provider_registry import ProviderRegistry


class _RealProvider:
    privacy_class = "local"

    def generate_chat(self, *, messages, tools, tool_choice):
        return {"text": "ok", "tool_calls": {"tool_calls": []}}


class _CloudProvider(_RealProvider):
    privacy_class = "cloud_provider"


class ProviderRegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = ProviderRegistry()

    def register(self, provider_id="local", provider=None, capabilities=None):
        return self.registry.register(
            provider_id=provider_id, version="1.0.0",
            privacy_class=getattr(provider or _RealProvider(), "privacy_class"),
            capabilities=capabilities or {"chat", "tool_calling"},
            factory=lambda: ProviderAgentAdapter(provider or _RealProvider()),
        )

    def test_register_and_resolve_verified_capability(self):
        self.register()
        adapter = self.registry.resolve("local", required_capabilities={"chat"})
        self.assertEqual(adapter.provider_id, "local")
        self.assertEqual(adapter.privacy_class, "local")

    def test_missing_capability_fails_before_factory_execution(self):
        calls = []
        self.registry.register(
            provider_id="text", version="1.0.0", privacy_class="local",
            capabilities={"chat"},
            factory=lambda: calls.append("constructed") or ProviderAgentAdapter(_RealProvider()),
        )
        with self.assertRaisesRegex(ValueError, "capability_requirement"):
            self.registry.resolve("text", required_capabilities={"vision"})
        self.assertEqual(calls, [])

    def test_cloud_provider_requires_explicit_opt_in(self):
        self.register("cloud", _CloudProvider())
        with self.assertRaisesRegex(PermissionError, "cloud_provider_requires"):
            self.registry.resolve("cloud")
        self.assertEqual(
            self.registry.resolve("cloud", cloud_opt_in=True).privacy_class,
            "cloud_provider",
        )

    def test_privacy_metadata_mismatch_fails_closed(self):
        self.registry.register(
            provider_id="misdeclared", version="1.0.0", privacy_class="local",
            capabilities={"chat"},
            factory=lambda: ProviderAgentAdapter(_CloudProvider()),
        )
        with self.assertRaisesRegex(ValueError, "privacy_metadata_mismatch"):
            self.registry.resolve("misdeclared", cloud_opt_in=True)

    def test_duplicate_ids_and_invalid_versions_are_rejected(self):
        self.register()
        with self.assertRaisesRegex(ValueError, "already_registered"):
            self.register()
        with self.assertRaisesRegex(ValueError, "version_invalid"):
            self.registry.register(
                provider_id="other", version="latest", privacy_class="local",
                capabilities={"chat"}, factory=lambda: ProviderAgentAdapter(_RealProvider()),
            )

    def test_mock_provider_is_rejected_at_resolution(self):
        class MockProvider(_RealProvider):
            pass
        self.registry.register(
            provider_id="mocked", version="1.0.0", privacy_class="local",
            capabilities={"chat"},
            factory=lambda: ProviderAgentAdapter(MockProvider()),
        )
        with self.assertRaisesRegex(ValueError, "mock_provider_forbidden"):
            self.registry.resolve("mocked")

    def test_describe_exposes_metadata_not_factory(self):
        self.register()
        result = self.registry.describe()
        self.assertEqual(result[0]["provider_id"], "local")
        self.assertNotIn("factory", result[0])


if __name__ == "__main__":
    unittest.main()
