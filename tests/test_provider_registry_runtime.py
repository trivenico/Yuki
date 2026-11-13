import tempfile
import unittest
from pathlib import Path

from providers.hybrid import HybridProvider
from providers.mock import MockProvider
from providers.ollama import OllamaProvider
from providers.openai_compatible import OpenAICompatibleProvider
from providers.profile_store import ProviderProfileStore
from yuki_providers import ProviderManager


class ProviderRegistryRuntimeTests(unittest.TestCase):
    def test_registry_reports_only_factory_supported_integrations(self):
        manager = ProviderManager(environ={})
        entries = {item["name"]: item for item in manager.list_providers()}
        self.assertNotIn("mock", entries)
        self.assertTrue(entries["ollama"]["implemented"])
        self.assertTrue(entries["hybrid"]["implemented"])
        self.assertTrue(entries["openai"]["implemented"])
        self.assertFalse(entries["anthropic"]["implemented"])
        self.assertTrue(entries["gemini"]["implemented"])

    def test_default_openai_adapter_is_constructible_without_credentials(self):
        adapter = ProviderManager(environ={}).create_selected_adapter()
        self.assertIsInstance(adapter, OpenAICompatibleProvider)
        self.assertFalse(adapter.is_available())

    def test_ollama_requires_configuration_and_failed_selection_is_atomic(self):
        manager = ProviderManager(environ={})
        message = manager.select("ollama")
        self.assertIn("Cannot select ollama", message)
        self.assertEqual(manager.current().name, "openai")

    def test_ollama_and_hybrid_use_shared_factory(self):
        manager = ProviderManager({"ollama_model": "test-model"}, environ={})
        self.assertEqual(manager.select("ollama"), "Selected provider: ollama")
        self.assertIsInstance(manager.create_selected_adapter(), OllamaProvider)
        self.assertEqual(manager.select("hybrid"), "Selected provider: hybrid")
        self.assertIsInstance(manager.create_selected_adapter(), HybridProvider)

    def test_selected_openai_provider_exposes_bounded_agent_model(self):
        from providers.agent_adapter import ProviderAgentAdapter
        manager = ProviderManager(environ={"OPENAI_API_KEY": "test-key"})
        self.assertIsInstance(manager.create_selected_agent_model(), ProviderAgentAdapter)

    def test_selected_ollama_provider_exposes_bounded_agent_model(self):
        from providers.agent_adapter import ProviderAgentAdapter
        manager = ProviderManager(
            {"provider": "ollama", "ollama_model": "test-model"}, environ={}
        )
        self.assertIsInstance(manager.create_selected_agent_model(), ProviderAgentAdapter)

    def test_text_only_hybrid_provider_fails_closed_for_agent_chat(self):
        manager = ProviderManager(
            {"provider": "hybrid", "ollama_model": "test-model"}, environ={}
        )
        with self.assertRaisesRegex(RuntimeError, "does not support structured agent chat"):
            manager.create_selected_agent_model()

    def test_unimplemented_provider_does_not_change_selection(self):
        manager = ProviderManager(environ={})
        message = manager.select("anthropic")
        self.assertIn("not implemented", message)
        self.assertEqual(manager.current().name, "openai")

    def test_custom_openai_compatible_endpoint_registers_and_routes_to_agent_runtime(self):
        from providers.agent_adapter import ProviderAgentAdapter
        manager = ProviderManager(environ={"CUSTOM_LLM_KEY": "secret-test-value"})
        manager.register_openai_compatible(
            name="my_gateway", base_url="https://gateway.example.test/v1",
            model="vendor/model-x", api_key_env="CUSTOM_LLM_KEY",
            allowed_hosts={"gateway.example.test"}, select=True,
        )
        adapter = manager.create_selected_adapter()
        self.assertIsInstance(adapter, OpenAICompatibleProvider)
        self.assertTrue(adapter.is_available())
        self.assertIsInstance(manager.create_selected_agent_model(), ProviderAgentAdapter)
        catalog = {item["name"]: item for item in manager.catalog()}
        self.assertTrue(catalog["my_gateway"]["implemented"])
        self.assertTrue(catalog["my_gateway"]["ready"])
        self.assertNotIn("secret-test-value", repr(catalog["my_gateway"]))

    def test_custom_endpoint_requires_https_and_exact_host_approval(self):
        manager = ProviderManager(environ={})
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            manager.register_openai_compatible(
                name="bad_http", base_url="http://gateway.example.test/v1",
                model="model-x", api_key_env="CUSTOM_LLM_KEY",
            )
        with self.assertRaisesRegex(ValueError, "explicitly included"):
            manager.register_openai_compatible(
                name="bad_host", base_url="https://gateway.example.test/v1",
                model="model-x", api_key_env="CUSTOM_LLM_KEY",
                allowed_hosts={"other.example.test"},
            )

    def test_custom_endpoint_does_not_accept_reserved_or_invalid_secret_names(self):
        manager = ProviderManager(environ={})
        with self.assertRaisesRegex(ValueError, "reserved"):
            manager.register_openai_compatible(
                name="openai", base_url="https://gateway.example.test/v1",
                model="model-x", api_key_env="CUSTOM_LLM_KEY",
            )
        with self.assertRaisesRegex(ValueError, "environment variable"):
            manager.register_openai_compatible(
                name="my_gateway", base_url="https://gateway.example.test/v1",
                model="model-x", api_key_env="secret-value",
            )

    def test_custom_factory_must_return_provider_contract(self):
        manager = ProviderManager(environ={})
        manager.register(type(manager.current())("custom", (), True), factory=lambda: object())
        with self.assertRaisesRegex(TypeError, "generate"):
            manager._build_adapter("custom")

    def test_persisted_vault_profile_loads_into_runtime_without_catalog_secret(self):
        class MemoryVault:
            def __init__(self):
                self.values = {}

            def set_secret(self, reference, secret):
                self.values[reference] = secret

            def get_secret(self, reference):
                return self.values.get(reference)

            def delete_secret(self, reference):
                self.values.pop(reference, None)

        with tempfile.TemporaryDirectory() as directory:
            vault = MemoryVault()
            store = ProviderProfileStore(Path(directory) / "profiles.sqlite", vault)
            store.save(
                owner_id="owner:alice", name="persisted_gateway",
                base_url="https://gateway.example.test/v1",
                model="vendor/model-x", allowed_host="gateway.example.test",
                api_key="vault-only-secret",
            )
            manager = ProviderManager(environ={})
            self.assertEqual(
                manager.load_persisted_profiles(store, "owner:alice"),
                ("persisted_gateway",),
            )
            self.assertEqual(
                manager.select("persisted_gateway"),
                "Selected provider: persisted_gateway",
            )
            adapter = manager.create_selected_adapter()
            self.assertIsInstance(adapter, OpenAICompatibleProvider)
            self.assertTrue(adapter.is_available())
            entry = next(
                item for item in manager.catalog()
                if item["name"] == "persisted_gateway"
            )
            self.assertTrue(entry["ready"])
            self.assertTrue(entry["credential_configured"])
            self.assertNotIn("vault-only-secret", repr(entry))
            self.assertNotIn(
                "vault-only-secret", repr(manager.compatible_profiles())
            )
            store.close()

    def test_persisted_profile_loader_rejects_wrong_owner_identity(self):
        class MemoryVault:
            def set_secret(self, reference, secret):
                pass

            def get_secret(self, reference):
                return None

            def delete_secret(self, reference):
                pass

        with tempfile.TemporaryDirectory() as directory:
            store = ProviderProfileStore(
                Path(directory) / "profiles.sqlite", MemoryVault()
            )
            store.save(
                owner_id="owner:alice", name="persisted_gateway",
                base_url="https://gateway.example.test/v1",
                model="vendor/model-x", allowed_host="gateway.example.test",
                api_key="secret",
            )
            manager = ProviderManager(environ={})
            self.assertEqual(
                manager.load_persisted_profiles(store, "owner:bob"), ()
            )
            self.assertNotIn(
                "persisted_gateway",
                {profile["name"] for profile in manager.list_providers()},
            )
            store.close()


if __name__ == "__main__":
    unittest.main()
