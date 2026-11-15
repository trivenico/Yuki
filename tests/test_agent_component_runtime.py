import unittest

from core.agent_component_runtime import build_agent_execution_component_runtime
from core.agent_execution_loop import AgentExecutionLoop
from core.component_catalog import DEFAULT_COMPONENTS
from core.component_registry import ComponentRegistry, ComponentRuntime
from core.guarded_tool_runtime import GuardedToolRuntime
from core.structured_tool_protocol import StructuredToolProtocol


class AllowPolicy:
    def authorize_tool(self, name):
        return False


class AgentExecutionComponentRuntimeTests(unittest.TestCase):
    def setUp(self):
        protocol = StructuredToolProtocol(GuardedToolRuntime({}, AllowPolicy()))
        self.loop = AgentExecutionLoop(
            lambda **kwargs: {
                "text": "ready",
                "tool_calls": {"tool_calls": []},
            },
            protocol,
        )

    def _registry_with_dependencies(self, agent_runtime):
        registry = ComponentRegistry()
        contracts = {item.component_id: item for item in DEFAULT_COMPONENTS}
        for component_id in ("identity-access", "provider-runtime"):
            contract = contracts[component_id]
            instance = object()
            registry.register(ComponentRuntime(
                contract=contract,
                instance=instance,
                start=lambda: None,
                close=lambda: None,
                health_check=lambda: True,
            ))
        registry.register(agent_runtime)
        return registry

    def test_registry_resolves_loop_and_health_tracks_lifecycle(self):
        runtime = build_agent_execution_component_runtime(self.loop)
        registry = self._registry_with_dependencies(runtime)

        self.assertIs(registry.resolve("agent-execution"), self.loop)
        self.assertEqual(registry.health(), {})
        started = registry.start_all()
        self.assertEqual(
            started,
            ("identity-access", "provider-runtime", "agent-execution"),
        )
        self.assertEqual(
            registry.health(),
            {
                "identity-access": True,
                "provider-runtime": True,
                "agent-execution": True,
            },
        )
        self.assertEqual(registry.close(), ())
        self.assertEqual(registry.started_components, ())

    def test_close_fences_agent_component_without_closing_shared_provider(self):
        runtime = build_agent_execution_component_runtime(self.loop)
        close_calls = []
        registry = self._registry_with_dependencies(runtime)
        registry.start_all()
        # The agent adapter owns only its own lifecycle state, not providers.
        registry.close()
        self.assertFalse(runtime.health_check())
        self.assertEqual(close_calls, [])

    def test_agent_health_is_false_before_start_and_after_close(self):
        runtime = build_agent_execution_component_runtime(self.loop)
        self.assertFalse(runtime.health_check())
        runtime.start()
        self.assertTrue(runtime.health_check())
        runtime.close()
        self.assertFalse(runtime.health_check())

    def test_invalid_loop_is_rejected(self):
        with self.assertRaisesRegex(TypeError, "AgentExecutionLoop"):
            build_agent_execution_component_runtime(object())

    def test_wrong_component_contract_is_rejected(self):
        provider_contract = next(
            item for item in DEFAULT_COMPONENTS
            if item.component_id == "provider-runtime"
        )
        with self.assertRaisesRegex(ValueError, "agent-execution"):
            build_agent_execution_component_runtime(
                self.loop, contract=provider_contract
            )


if __name__ == "__main__":
    unittest.main()
