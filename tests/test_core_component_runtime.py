import tempfile
import unittest
from pathlib import Path

from core.agent_execution_loop import AgentExecutionLoop
from core.auth import BuiltInAuthService
from core.core_component_runtime import build_core_component_registry
from core.conversation_application_runtime import build_conversation_application_runtime
from core.conversation_store import ConversationStore
from core.network_firewall import NetworkFirewall
from core.guarded_tool_runtime import GuardedToolRuntime
from core.structured_tool_protocol import StructuredToolProtocol


class _NoApprovals:
    def authorize_tool(self, name):
        return False


class FakeProvider:
    def __init__(self):
        self.closed = 0
    def generate(self, prompt):
        return prompt
    def is_available(self):
        return True
    def close(self):
        self.closed += 1


class CoreComponentCompositionTests(unittest.TestCase):
    def _loop(self):
        return AgentExecutionLoop(
            lambda **kwargs: {"text": "ready", "tool_calls": {"tool_calls": []}},
            StructuredToolProtocol(GuardedToolRuntime({}, _NoApprovals())),
        )

    def test_registers_and_starts_real_core_dependency_graph(self):
        with tempfile.TemporaryDirectory() as directory:
            auth = BuiltInAuthService(str(Path(directory) / "auth.sqlite3"), password_iterations=100_000)
            provider = FakeProvider()
            loop = self._loop()
            registry = build_core_component_registry(auth_service=auth, provider=provider, agent_loop=loop)
            self.assertEqual(registry.started_components, ())
            self.assertIs(registry.resolve("identity-access"), auth)
            self.assertIs(registry.resolve("provider-runtime"), provider)
            self.assertIs(registry.resolve("agent-execution"), loop)
            self.assertEqual(set(registry.start_all()), {"identity-access", "provider-runtime", "agent-execution"})
            self.assertEqual(registry.health(), {"identity-access": True, "provider-runtime": True, "agent-execution": True})
            self.assertEqual(registry.close(), ())
            self.assertEqual(provider.closed, 1)

    def test_network_firewall_is_registered_in_foundational_core_graph(self):
        with tempfile.TemporaryDirectory() as directory:
            auth = BuiltInAuthService(str(Path(directory) / "auth.sqlite3"), password_iterations=100_000)
            provider = FakeProvider()
            firewall = NetworkFirewall(allowed_hosts=("api.example.com",))
            registry = build_core_component_registry(
                auth_service=auth, provider=provider, agent_loop=self._loop(),
                network_firewall=firewall,
            )
            self.assertIs(registry.resolve("network-firewall"), firewall)
            started = registry.start_all()
            self.assertLess(started.index("identity-access"), started.index("network-firewall"))
            self.assertTrue(registry.health()["network-firewall"])
            registry.close()
            self.assertEqual(provider.closed, 1)

    def test_unavailable_provider_rolls_back_started_dependencies(self):
        class UnavailableProvider(FakeProvider):
            def is_available(self):
                return False
        with tempfile.TemporaryDirectory() as directory:
            auth = BuiltInAuthService(str(Path(directory) / "auth.sqlite3"), password_iterations=100_000)
            provider = UnavailableProvider()
            registry = build_core_component_registry(auth_service=auth, provider=provider, agent_loop=self._loop())
            with self.assertRaisesRegex(RuntimeError, "startup failed"):
                registry.start_all()
            self.assertEqual(registry.started_components, ())
            self.assertEqual(provider.closed, 1)
            with self.assertRaisesRegex(RuntimeError, "registry is closed"):
                registry.health()

    def test_configuration_runtime_is_registered_before_core_services(self):
        with tempfile.TemporaryDirectory() as directory:
            auth = BuiltInAuthService(str(Path(directory) / "auth.sqlite3"), password_iterations=100_000)
            provider = FakeProvider()
            config = {
                "workspace": directory,
                "provider": "mock",
                "storage_backend": "sqlite",
            }
            registry = build_core_component_registry(
                auth_service=auth, provider=provider, agent_loop=self._loop(),
                config=config,
            )
            snapshot = registry.resolve("configuration-runtime")
            self.assertEqual(snapshot.get("provider"), "mock")
            started = registry.start_all()
            self.assertLess(started.index("configuration-runtime"), started.index("provider-runtime"))
            self.assertTrue(registry.health()["configuration-runtime"])
            registry.close()
            self.assertEqual(provider.closed, 1)

    def test_owner_agent_factory_is_registered_as_separate_component(self):
        with tempfile.TemporaryDirectory() as directory:
            auth = BuiltInAuthService(str(Path(directory) / "auth.sqlite3"), password_iterations=100_000)
            provider = FakeProvider()
            factory = lambda owner_id: self._loop()
            store = ConversationStore(Path(directory) / "conversations.sqlite3")
            application = build_conversation_application_runtime(
                {}, agent_loop=self._loop(), agent_loop_for_owner=factory,
                store_factory=lambda config: store,
            )
            registry = build_core_component_registry(
                auth_service=auth, provider=provider, agent_loop=application.conversations.agent_loop,
                application_runtime=application,
            )
            self.assertIs(registry.resolve("owner-agent-execution"), factory)
            started = registry.start_all()
            self.assertLess(started.index("agent-execution"), started.index("owner-agent-execution"))
            self.assertTrue(registry.health()["owner-agent-execution"])
            registry.close()
            self.assertTrue(application.closed)
            self.assertEqual(provider.closed, 1)

    def test_invalid_provider_is_rejected_before_registry_is_returned(self):
        with tempfile.TemporaryDirectory() as directory:
            auth = BuiltInAuthService(str(Path(directory) / "auth.sqlite3"), password_iterations=100_000)
            with self.assertRaisesRegex(TypeError, "provider"):
                build_core_component_registry(auth_service=auth, provider=object(), agent_loop=self._loop())

    def test_full_graph_starts_and_application_owner_closes_shared_conversations(self):
        with tempfile.TemporaryDirectory() as directory:
            auth = BuiltInAuthService(str(Path(directory) / "auth.sqlite3"), password_iterations=100_000)
            provider = FakeProvider()
            loop = self._loop()
            store = ConversationStore(Path(directory) / "conversations.sqlite3")
            application = build_conversation_application_runtime(
                {}, agent_loop=loop, store_factory=lambda config: store,
            )
            registry = build_core_component_registry(
                auth_service=auth, provider=provider, agent_loop=loop,
                application_runtime=application,
            )
            started = registry.start_all()
            self.assertEqual(set(started), {
                "identity-access", "provider-runtime", "agent-execution",
                "durable-conversations", "application-composition",
            })
            self.assertTrue(all(registry.health().values()))
            self.assertIs(registry.resolve("durable-conversations"), application.conversations)
            self.assertIs(registry.resolve("application-composition"), application)
            self.assertEqual(registry.close(), ())
            self.assertTrue(application._closed)
            self.assertEqual(provider.closed, 1)
            with self.assertRaisesRegex(RuntimeError, "service is closed"):
                application.conversations.list(owner_id="closed-owner")

    def test_durable_storage_probe_failure_rolls_back_full_graph(self):
        with tempfile.TemporaryDirectory() as directory:
            auth = BuiltInAuthService(str(Path(directory) / "auth.sqlite3"), password_iterations=100_000)
            provider = FakeProvider()
            loop = self._loop()
            store = ConversationStore(Path(directory) / "unhealthy.sqlite3")
            application = build_conversation_application_runtime(
                {}, agent_loop=loop, store_factory=lambda config: store,
            )
            store.health_check = lambda: False
            registry = build_core_component_registry(
                auth_service=auth, provider=provider, agent_loop=loop,
                application_runtime=application,
            )
            with self.assertRaisesRegex(RuntimeError, "startup failed: durable-conversations"):
                registry.start_all()
            self.assertEqual(registry.started_components, ())
            self.assertEqual(provider.closed, 1)
            self.assertTrue(application._closed)


if __name__ == "__main__":
    unittest.main()
