import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from providers.runtime_manager import ProviderRuntimeManager
from core.authorization import AuthorizationDecision, Principal
from core.authorization_gateway import AuthorizationGateway
from providers.authorized_agent_adapter import AuthorizedProviderAgentAdapter


def _catalog(*, config, environ):
    return [
        {"name": "openai", "label": "OpenAI", "implemented": True, "ready": True,
         "privacy": "cloud_provider", "selected": config.get("provider") == "openai"},
        {"name": "ollama", "label": "Ollama", "implemented": True, "ready": bool(environ.get("YUKI_OLLAMA_MODEL")),
         "privacy": "local_runtime", "selected": config.get("provider") == "ollama"},
        {"name": "anthropic", "label": "Anthropic", "implemented": False, "ready": False,
         "privacy": "cloud_provider", "selected": config.get("provider") == "anthropic"},
    ]


class ProviderRuntimeManagerTests(unittest.TestCase):
    def test_selection_is_persisted_and_reloaded_without_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "provider-selection.json"
            config = {"provider": "openai"}
            adapter = SimpleNamespace(provider="initial")
            env = {"YUKI_OLLAMA_MODEL": "qwen3", "OPENAI_API_KEY": "never-persist"}
            factory = lambda cfg, environ: "provider:" + cfg["provider"]
            manager = ProviderRuntimeManager(
                config, adapter, path, environ=env,
                provider_factory=factory, catalog_factory=_catalog,
            )
            selected = manager.select("ollama")
            self.assertEqual(selected["name"], "ollama")
            self.assertEqual(adapter.provider, "provider:ollama")
            self.assertEqual(config["provider"], "ollama")
            self.assertEqual(json.loads(path.read_text()), {"schema_version": 1, "provider": "ollama"})
            self.assertNotIn("never-persist", path.read_text())
            restored_adapter = SimpleNamespace(provider="initial")
            restored = ProviderRuntimeManager(
                {"provider": "openai"}, restored_adapter, path, environ=env,
                provider_factory=factory, catalog_factory=_catalog,
            )
            self.assertEqual(restored_adapter.provider, "provider:ollama")
            self.assertEqual(restored.config["provider"], "ollama")
            self.assertEqual(restored.catalog()[1]["selected"], True)

    def test_unready_and_unimplemented_providers_are_rejected_without_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            config = {"provider": "openai"}
            adapter = SimpleNamespace(provider="original")
            manager = ProviderRuntimeManager(
                config, adapter, Path(directory) / "selection.json",
                environ={}, provider_factory=lambda cfg, environ: "new",
                catalog_factory=_catalog,
            )
            for name, error in (("ollama", "provider_not_ready"), ("anthropic", "provider_not_implemented")):
                with self.assertRaisesRegex(ValueError, error):
                    manager.select(name)
            self.assertEqual(adapter.provider, "original")
            self.assertEqual(config["provider"], "openai")
            self.assertFalse((Path(directory) / "selection.json").exists())


    def test_provider_selection_preserves_authorized_adapter_and_refreshes_identity(self):
        class Policy:
            def authorize(self, request):
                return AuthorizationDecision(True, "decision-1", "revision-1", "allowed")

        class Provider:
            def __init__(self, name):
                self.model = name + "-model"
            def generate_chat(self, **_kwargs):
                return {"text": "ok", "tool_calls": {"tool_calls": []}}

        with tempfile.TemporaryDirectory() as directory:
            gateway = AuthorizationGateway(Policy(), audit=lambda *_: None)
            principal = Principal("owner-1", "human", permissions=frozenset({"inference:invoke"}))
            adapter = AuthorizedProviderAgentAdapter(
                Provider("openai"), authorization_gateway=gateway,
                principal=principal, provider_name="openai", policy_revision="revision-1",
            )
            manager = ProviderRuntimeManager(
                {"provider": "openai"}, adapter, Path(directory) / "selection.json",
                environ={"YUKI_OLLAMA_MODEL": "qwen3"},
                provider_factory=lambda cfg, environ: Provider(cfg["provider"]),
                catalog_factory=_catalog,
            )
            manager.select("ollama")
            self.assertEqual(adapter.provider_name, "ollama")
            self.assertEqual(adapter.model_id, "ollama-model")
            self.assertIs(adapter.authorization_gateway, gateway)
            self.assertEqual(adapter.principal, principal)

    def test_corrupt_persisted_state_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "provider-selection.json"
            path.write_text('{"provider":"ollama","schema_version":99}')
            with self.assertRaisesRegex(RuntimeError, "unsupported schema"):
                ProviderRuntimeManager(
                    {"provider": "openai"}, SimpleNamespace(provider="initial"), path,
                    environ={}, provider_factory=lambda cfg, environ: "new",
                    catalog_factory=_catalog,
                )


if __name__ == "__main__":
    unittest.main()
