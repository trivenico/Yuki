import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from providers.runtime_manager import ProviderRuntimeManager
from providers.lazy_provider import LazyProvider


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

    def test_persisted_selection_can_restore_without_constructing_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "provider-selection.json"
            path.write_text(json.dumps({"schema_version": 1, "provider": "ollama"}))
            config = {"provider": "openai"}
            calls = []
            class SelectedProvider:
                def generate_chat(self, *, messages, tools, tool_choice="auto"):
                    return "provider:" + config["provider"]
            adapter = SimpleNamespace(provider=LazyProvider(
                config, environ={"YUKI_OLLAMA_MODEL": "qwen3"},
                provider_factory=lambda cfg, environ: (
                    calls.append(cfg["provider"]), SelectedProvider()
                )[1],
            ))
            manager = ProviderRuntimeManager(
                config, adapter, path,
                environ={"YUKI_OLLAMA_MODEL": "qwen3"},
                provider_factory=lambda cfg, environ: calls.append("manager-factory"),
                catalog_factory=_catalog,
                defer_persisted_provider_initialization=True,
            )
            self.assertEqual(config["provider"], "ollama")
            self.assertEqual(calls, [])
            self.assertEqual(adapter.provider.generate_chat(
                messages=[{"role": "user", "content": "hi"}], tools=[],
            ), "provider:ollama")
            self.assertEqual(calls, ["ollama"])

    def test_explicit_selection_still_constructs_and_validates_provider_eagerly(self):
        with tempfile.TemporaryDirectory() as directory:
            config = {"provider": "openai"}
            calls = []
            class SelectedProvider:
                def generate_chat(self, *, messages, tools, tool_choice="auto"):
                    return {"text": "selected"}
            lazy = LazyProvider(
                config, environ={"YUKI_OLLAMA_MODEL": "qwen3"},
                provider_factory=lambda cfg, environ: calls.append("lazy") or SelectedProvider(),
            )
            adapter = SimpleNamespace(provider=lazy)
            manager = ProviderRuntimeManager(
                config, adapter, Path(directory) / "selection.json",
                environ={"YUKI_OLLAMA_MODEL": "qwen3"},
                provider_factory=lambda cfg, environ: (
                    calls.append("admin-selection:" + cfg["provider"]), SelectedProvider()
                )[1],
                catalog_factory=_catalog,
                defer_persisted_provider_initialization=True,
            )
            selected = manager.select("ollama")
            self.assertEqual(selected["name"], "ollama")
            self.assertEqual(calls, ["admin-selection:ollama"])
            self.assertIsInstance(adapter.provider, SelectedProvider)
            self.assertEqual(config["provider"], "ollama")

    def test_unready_persisted_provider_does_not_block_deferred_startup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "provider-selection.json"
            path.write_text(json.dumps({"schema_version": 1, "provider": "openai"}))
            config = {"provider": "ollama"}
            calls = []
            catalog = lambda config, environ: [
                {"name": "openai", "implemented": True, "ready": False}
            ]
            lazy = LazyProvider(
                config, environ={},
                provider_factory=lambda cfg, environ: calls.append(True),
            )
            manager = ProviderRuntimeManager(
                config, SimpleNamespace(provider=lazy), path,
                environ={}, provider_factory=lambda cfg, environ: calls.append("manager"),
                catalog_factory=catalog,
                defer_persisted_provider_initialization=True,
            )
            self.assertEqual(config["provider"], "openai")
            self.assertEqual(calls, [])
            with self.assertRaisesRegex(RuntimeError, "^configured_provider_unavailable$"):
                lazy.generate_chat(messages=[{"role": "user", "content": "hello"}], tools=[])
            self.assertEqual(calls, [True])

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


    def test_replaced_provider_is_closed_only_at_adapter_shutdown(self):
        from providers.agent_adapter import ProviderAgentAdapter

        class Provider:
            def __init__(self, name):
                self.name = name
                self.closed = 0
            def generate_chat(self, *, messages, tools, tool_choice="auto"):
                return {"text": self.name}
            def close(self):
                self.closed += 1

        with tempfile.TemporaryDirectory() as directory:
            config = {"provider": "openai"}
            first, second = Provider("first"), Provider("second")
            adapter = ProviderAgentAdapter(first)
            manager = ProviderRuntimeManager(
                config, adapter, Path(directory) / "selection.json",
                environ={"YUKI_OLLAMA_MODEL": "qwen3"},
                provider_factory=lambda cfg, environ: second,
                catalog_factory=_catalog,
                defer_persisted_provider_initialization=True,
            )
            manager.select("ollama")
            self.assertIs(adapter.provider, second)
            self.assertEqual(first.closed, 0)
            self.assertEqual(second.closed, 0)
            adapter.close()
            self.assertEqual(first.closed, 1)
            self.assertEqual(second.closed, 1)

    def test_provider_adapter_retries_only_failed_provider_cleanup(self):
        from providers.agent_adapter import ProviderAgentAdapter

        class Provider:
            def __init__(self, fail_once=False):
                self.calls = 0
                self.fail_once = fail_once
            def generate_chat(self, *, messages, tools, tool_choice="auto"):
                return {"text": "ok"}
            def close(self):
                self.calls += 1
                if self.fail_once and self.calls == 1:
                    raise OSError("private provider detail")

        old, current = Provider(fail_once=True), Provider()
        adapter = ProviderAgentAdapter(old)
        adapter.replace_provider(current)
        with self.assertRaisesRegex(RuntimeError, "^provider_adapter_shutdown_failed$"):
            adapter.close()
        self.assertEqual(old.calls, 1)
        self.assertEqual(current.calls, 1)
        adapter.close()
        self.assertEqual(old.calls, 2)
        self.assertEqual(current.calls, 1)


if __name__ == "__main__":
    unittest.main()
