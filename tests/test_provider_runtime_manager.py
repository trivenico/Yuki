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

    def test_close_refuses_to_close_provider_while_request_lease_is_active(self):
        with tempfile.TemporaryDirectory() as directory:
            events = []
            class Provider:
                def close(self): events.append("closed")
            provider = Provider()
            manager = ProviderRuntimeManager(
                {"provider": "openai"}, SimpleNamespace(provider=provider),
                Path(directory) / "selection.json", environ={},
                provider_factory=lambda cfg, environ: provider, catalog_factory=_catalog,
            )
            with manager.lease_provider() as leased:
                self.assertIs(leased, provider)
                with self.assertRaisesRegex(RuntimeError, "active provider lease"):
                    manager.close()
                self.assertEqual(events, [])
                with self.assertRaisesRegex(RuntimeError, "shutting down"):
                    with manager.lease_provider():
                        pass
                with self.assertRaisesRegex(RuntimeError, "shutting down"):
                    manager.select("openai")
            manager.close()
            self.assertEqual(events, ["closed"])

    def test_provider_lease_is_released_when_request_raises(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = SimpleNamespace()
            manager = ProviderRuntimeManager(
                {"provider": "openai"}, SimpleNamespace(provider=provider),
                Path(directory) / "selection.json", environ={},
                provider_factory=lambda cfg, environ: provider, catalog_factory=_catalog,
            )
            with self.assertRaisesRegex(ValueError, "request failed"):
                with manager.lease_provider():
                    raise ValueError("request failed")
            manager.close()
    def test_agent_adapter_holds_lease_for_entire_chat_call(self):
        from threading import Event, Thread
        from providers.agent_adapter import ProviderAgentAdapter
        with tempfile.TemporaryDirectory() as directory:
            started, release = Event(), Event()
            events = []
            class Provider:
                def generate_chat(self, **kwargs):
                    started.set()
                    if not release.wait(2): raise TimeoutError("test release timeout")
                    return {"text": "ok", "tool_calls": {"tool_calls": []}}
                def close(self): events.append("closed")
            provider = Provider()
            manager = ProviderRuntimeManager(
                {"provider": "openai"}, SimpleNamespace(provider=provider),
                Path(directory) / "selection.json", environ={},
                provider_factory=lambda cfg, environ: provider, catalog_factory=_catalog,
            )
            adapter = ProviderAgentAdapter(provider)
            adapter.provider_manager = manager
            errors = []
            thread = Thread(target=lambda: adapter(messages=[{"role": "user", "content": "hi"}], tools=[]))
            thread.start()
            self.assertTrue(started.wait(2))
            with self.assertRaisesRegex(RuntimeError, "active provider lease"): manager.close()
            self.assertEqual(events, [])
            release.set()
            thread.join(2)
            self.assertFalse(thread.is_alive())
            manager.close()
            self.assertEqual(events, ["closed"])

    def test_agent_adapter_stream_lease_lives_until_stream_is_closed(self):
        from providers.agent_adapter import ProviderAgentAdapter
        with tempfile.TemporaryDirectory() as directory:
            events = []
            class Provider:
                def generate_chat_stream(self, **kwargs):
                    yield {"type": "token", "text": "a"}
                    yield {"type": "token", "text": "b"}
                def close(self): events.append("closed")
            provider = Provider()
            manager = ProviderRuntimeManager(
                {"provider": "openai"}, SimpleNamespace(provider=provider),
                Path(directory) / "selection.json", environ={},
                provider_factory=lambda cfg, environ: provider, catalog_factory=_catalog,
            )
            adapter = ProviderAgentAdapter(provider)
            adapter.provider_manager = manager
            stream = adapter.stream_model(messages=[{"role": "user", "content": "hi"}], tools=[])
            self.assertEqual(next(stream), {"type": "token", "text": "a"})
            with self.assertRaisesRegex(RuntimeError, "active provider lease"): manager.close()
            stream.close()
            manager.close()
            self.assertEqual(events, ["closed"])
    def test_close_releases_active_and_replaced_provider_instances_once(self):
        with tempfile.TemporaryDirectory() as directory:
            events = []
            class Provider:
                def __init__(self, name):
                    self.name = name
                def close(self):
                    events.append(self.name)
            initial = Provider("initial")
            adapter = SimpleNamespace(provider=initial)
            manager = ProviderRuntimeManager(
                {"provider": "openai"}, adapter, Path(directory) / "selection.json",
                environ={"YUKI_OLLAMA_MODEL": "qwen3"},
                provider_factory=lambda cfg, environ: Provider(cfg["provider"]),
                catalog_factory=_catalog,
            )
            manager.select("ollama")
            manager.close()
            manager.close()
            self.assertEqual(events, ["ollama", "initial"])
            with self.assertRaisesRegex(RuntimeError, "closed"):
                manager.catalog()
            with self.assertRaisesRegex(RuntimeError, "closed"):
                manager.select("openai")

if __name__ == "__main__":
    unittest.main()
