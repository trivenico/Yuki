import unittest

from providers.factory import create_provider
from providers.gemini_cli import GeminiCLIProvider
from providers.hybrid import HybridProvider
from providers.mock import MockProvider
from providers.openai_compatible import OpenAICompatibleProvider
from providers.ollama import OllamaProvider


class ProviderFactoryTests(unittest.TestCase):
    def test_default_factory_uses_real_openai_provider(self):
        self.assertIsInstance(create_provider({}), OpenAICompatibleProvider)

    def test_local_provider_requires_model_configuration(self):
        with self.assertRaisesRegex(ValueError, "model"):
            create_provider({"provider": "ollama", "ollama_model": ""})

    def test_gemini_provider_is_constructible_without_cli_installation(self):
        provider = create_provider({"provider": "gemini"}, environ={})
        self.assertIsInstance(provider, GeminiCLIProvider)
        self.assertFalse(provider.is_available())

    def test_hybrid_routes_to_local_without_explicit_cloud_tasks(self):
        provider = create_provider(
            {"provider": "hybrid", "ollama_model": "local-model"},
            environ={},
        )
        self.assertIsInstance(provider, HybridProvider)
        self.assertIsInstance(provider.local_provider, OllamaProvider)
        self.assertIsNone(provider.cloud_provider)
        self.assertEqual(provider.cloud_tasks, frozenset())


    def test_shared_firewall_is_injected_into_remote_ollama_transport(self):
        from core.network_firewall import NetworkFirewall

        firewall = NetworkFirewall(allowed_hosts=("ollama.example.com",))
        provider = create_provider(
            {"provider": "ollama", "ollama_base_url": "https://ollama.example.com",
             "ollama_model": "local-model"},
            environ={}, firewall_policy=firewall,
        )
        self.assertIs(provider._transport.firewall_policy, firewall)
        self.assertEqual(
            provider._transport.allowed_hosts, frozenset({"ollama.example.com"}),
        )

    def test_remote_ollama_fails_closed_when_host_is_not_in_shared_firewall(self):
        from core.network_firewall import NetworkFirewall

        firewall = NetworkFirewall(allowed_hosts=("api.openai.com",))
        with self.assertRaisesRegex(ValueError, "firewall policy"):
            create_provider(
                {"provider": "ollama", "ollama_base_url": "https://ollama.example.com",
                 "ollama_model": "local-model"},
                environ={}, firewall_policy=firewall,
            )

    def test_shared_firewall_is_injected_into_hybrid_remote_ollama_transport(self):
        from core.network_firewall import NetworkFirewall

        firewall = NetworkFirewall(allowed_hosts=("ollama.example.com", "api.openai.com"))
        provider = create_provider(
            {"provider": "hybrid", "ollama_base_url": "https://ollama.example.com",
             "ollama_model": "local-model"},
            environ={}, firewall_policy=firewall,
        )
        self.assertIs(provider.local_provider._transport.firewall_policy, firewall)

    def test_unknown_provider_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "provider must be one of"):
            create_provider({"provider": "automatic-cloud-fallback"})

    def test_shared_firewall_is_injected_into_default_cloud_provider_transport(self):
        from core.network_firewall import NetworkFirewall

        firewall = NetworkFirewall(allowed_hosts=("api.openai.com",))
        provider = create_provider({}, environ={}, firewall_policy=firewall)
        self.assertIs(provider._transport.firewall_policy, firewall)
        self.assertEqual(provider._transport.allowed_hosts, frozenset({"api.openai.com"}))

    def test_shared_firewall_is_injected_into_hybrid_cloud_provider(self):
        from core.network_firewall import NetworkFirewall

        firewall = NetworkFirewall(allowed_hosts=("api.openai.com",))
        provider = create_provider(
            {"provider": "hybrid", "ollama_model": "local-model"},
            environ={"YUKI_CLOUD_MODEL": "cloud-model", "OPENAI_API_KEY": "test-key"},
            firewall_policy=firewall,
        )
        self.assertIs(provider.cloud_provider._transport.firewall_policy, firewall)
        self.assertEqual(provider.cloud_provider._transport.allowed_hosts, frozenset({"api.openai.com"}))


if __name__ == "__main__":
    unittest.main()
