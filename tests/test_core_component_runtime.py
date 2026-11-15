import tempfile
import unittest
from pathlib import Path

from core.agent_execution_loop import AgentExecutionLoop
from core.auth import BuiltInAuthService
from core.core_component_runtime import build_core_component_registry
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

    def test_invalid_provider_is_rejected_before_registry_is_returned(self):
        with tempfile.TemporaryDirectory() as directory:
            auth = BuiltInAuthService(str(Path(directory) / "auth.sqlite3"), password_iterations=100_000)
            with self.assertRaisesRegex(TypeError, "provider"):
                build_core_component_registry(auth_service=auth, provider=object(), agent_loop=self._loop())


if __name__ == "__main__":
    unittest.main()
