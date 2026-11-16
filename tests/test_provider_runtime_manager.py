import json
import threading
from concurrent.futures import ThreadPoolExecutor
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from providers.runtime_manager import ProviderRuntimeManager
from providers.agent_adapter import ProviderAgentAdapter
from core.network_firewall import NetworkFirewall


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

    def test_switch_closes_previous_provider_after_new_provider_is_active(self):
        class ManagedProvider:
            def __init__(self, name):
                self.name = name
                self.closed = 0
            def close(self):
                self.closed += 1

        with tempfile.TemporaryDirectory() as directory:
            previous = ManagedProvider("previous")
            candidate = ManagedProvider("candidate")
            adapter = SimpleNamespace(provider=previous)
            config = {"provider": "openai"}
            manager = ProviderRuntimeManager(
                config, adapter, Path(directory) / "selection.json",
                environ={"YUKI_OLLAMA_MODEL": "qwen3"}, provider_factory=lambda cfg, environ: candidate,
                catalog_factory=_catalog,
            )
            manager.select("ollama")
            self.assertIs(adapter.provider, candidate)
            self.assertEqual(config["provider"], "ollama")
            self.assertEqual(previous.closed, 1)
            self.assertEqual(candidate.closed, 0)

    def test_failed_persistence_does_not_close_or_replace_active_provider(self):
        class ManagedProvider:
            def __init__(self):
                self.closed = 0
            def close(self):
                self.closed += 1

        with tempfile.TemporaryDirectory() as directory:
            previous = ManagedProvider()
            candidate = ManagedProvider()
            adapter = SimpleNamespace(provider=previous)
            manager = ProviderRuntimeManager(
                {"provider": "openai"}, adapter, Path(directory) / "missing" / "selection.json",
                environ={"YUKI_OLLAMA_MODEL": "qwen3"}, provider_factory=lambda cfg, environ: candidate,
                catalog_factory=_catalog,
            )
            manager._persist = lambda name: (_ for _ in ()).throw(OSError("disk unavailable"))
            with self.assertRaises(OSError):
                manager.select("ollama")
            self.assertIs(adapter.provider, previous)
            self.assertEqual(previous.closed, 0)
            self.assertEqual(candidate.closed, 1)

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



    def test_switch_waits_for_active_adapter_request_before_closing_old_provider(self):
        entered = threading.Event()
        release = threading.Event()

        class ManagedProvider:
            def __init__(self, name, block=False):
                self.name = name
                self.block = block
                self.closed = 0
            def generate_chat(self, *, messages, tools, tool_choice):
                if self.block:
                    entered.set()
                    if not release.wait(timeout=5):
                        raise TimeoutError("test did not release provider")
                return {"text": self.name, "tool_calls": {"tool_calls": []}}
            def close(self):
                self.closed += 1

        with tempfile.TemporaryDirectory() as directory:
            previous = ManagedProvider("previous", block=True)
            candidate = ManagedProvider("candidate")
            adapter = ProviderAgentAdapter(previous)
            manager = ProviderRuntimeManager(
                {"provider": "openai"}, adapter, Path(directory) / "selection.json",
                environ={"YUKI_OLLAMA_MODEL": "qwen3"},
                provider_factory=lambda cfg, environ: candidate,
                catalog_factory=_catalog,
            )
            with ThreadPoolExecutor(max_workers=2) as pool:
                request = pool.submit(
                    adapter, messages=[{"role": "user", "content": "x"}], tools=[]
                )
                self.assertTrue(entered.wait(timeout=3))
                switch = pool.submit(manager.select, "ollama")
                self.assertFalse(switch.done())
                self.assertEqual(previous.closed, 0)
                release.set()
                self.assertEqual(request.result(timeout=5)["text"], "previous")
                self.assertEqual(switch.result(timeout=5)["name"], "ollama")
            self.assertEqual(previous.closed, 1)
            self.assertIs(adapter.provider, candidate)


    def test_shared_firewall_policy_reaches_persistent_provider_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            firewall = NetworkFirewall(allowed_hosts={"api.openai.com"})
            captured = []
            def factory(config, *, environ, firewall_policy=None):
                captured.append(firewall_policy)
                return SimpleNamespace(provider=config["provider"])
            adapter = SimpleNamespace(provider=SimpleNamespace(provider="openai"))
            manager = ProviderRuntimeManager(
                {"provider": "openai"}, adapter,
                Path(directory) / "provider-selection.json",
                environ={"YUKI_OLLAMA_MODEL": "qwen3"},
                provider_factory=factory, catalog_factory=_catalog,
                firewall_policy=firewall,
            )
            manager.select("ollama")
            self.assertIs(captured[-1], firewall)
            self.assertEqual(adapter.provider.provider, "ollama")

    def test_shared_firewall_policy_reaches_persisted_provider_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "provider-selection.json"
            path.write_text(json.dumps({"schema_version": 1, "provider": "ollama"}))
            firewall = NetworkFirewall(allowed_hosts={"api.openai.com"})
            captured = []
            def factory(config, *, environ, firewall_policy=None):
                captured.append(firewall_policy)
                return SimpleNamespace(provider=config["provider"])
            adapter = SimpleNamespace(provider=SimpleNamespace(provider="openai"))
            ProviderRuntimeManager(
                {"provider": "openai"}, adapter, path,
                environ={"YUKI_OLLAMA_MODEL": "qwen3"},
                provider_factory=factory, catalog_factory=_catalog,
                firewall_policy=firewall,
            )
            self.assertEqual(captured, [firewall])
            self.assertEqual(adapter.provider.provider, "ollama")

if __name__ == "__main__":
    unittest.main()
