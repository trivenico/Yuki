import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from providers.runtime_manager import ProviderRuntimeManager


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
