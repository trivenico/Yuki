import unittest

from core.mcp_oauth_provider_bootstrap import PreparedOAuthProvider
from core.oauth_provider_registry import (
    OAuthProviderPresentation,
    PreparedOAuthProviderRegistry,
)


class Service:
    def begin(self, **kwargs): return {}
    def complete(self, **kwargs): return {}


def prepared(provider_id="sample"):
    return PreparedOAuthProvider(
        provider_id=provider_id,
        issuer="https://auth.example.com",
        resource="https://api.example.com/mcp",
        client_id="public-client-id",
        registration_method="dynamic_client_registration",
        redirect_uri=f"https://yuki.example.com/oauth/callback/{provider_id}",
        scopes=("jobs.read",),
        authorization_service=Service(),
    )


class ProviderRegistryTests(unittest.TestCase):
    def test_resolver_and_catalog_expose_only_safe_provider_projection(self):
        registry = PreparedOAuthProviderRegistry(
            {"sample": prepared()},
            {"sample": OAuthProviderPresentation("Sample", "Read-only integration")},
        )
        self.assertIsInstance(registry.authorization_service_for_provider("sample"), Service)
        self.assertIsNone(registry.authorization_service_for_provider("unknown"))
        self.assertEqual(registry.provider_ids(), ("sample",))
        item = registry.provider_catalog()[0]
        self.assertEqual(item, {
            "provider_id": "sample",
            "display_name": "Sample",
            "description": "Read-only integration",
            "available": True,
        })
        self.assertNotIn("client_id", item)
        self.assertNotIn("issuer", item)
        self.assertNotIn("redirect_uri", item)

    def test_mismatched_keys_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "identity"):
            PreparedOAuthProviderRegistry(
                {"other": prepared("sample")},
                {"other": OAuthProviderPresentation("Sample")},
            )

    def test_every_provider_requires_presentation(self):
        with self.assertRaisesRegex(ValueError, "presentation"):
            PreparedOAuthProviderRegistry({"sample": prepared()}, {})

    def test_unprepared_provider_is_rejected(self):
        with self.assertRaisesRegex(TypeError, "successfully prepared"):
            PreparedOAuthProviderRegistry(
                {"sample": object()},
                {"sample": OAuthProviderPresentation("Sample")},
            )

    def test_registry_is_immutable_and_unknown_provider_fails_closed(self):
        registry = PreparedOAuthProviderRegistry(
            {"sample": prepared()},
            {"sample": OAuthProviderPresentation("Sample")},
        )
        with self.assertRaises(TypeError):
            registry._services["other"] = Service()
        self.assertIsNone(registry.authorization_service_for_provider("../sample"))

    def test_presentation_rejects_control_characters(self):
        with self.assertRaisesRegex(ValueError, "display_name"):
            OAuthProviderPresentation("Sample\\nInjected")


if __name__ == "__main__":
    unittest.main()
