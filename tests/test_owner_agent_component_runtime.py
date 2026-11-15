import unittest

from core.agent_execution_loop import AgentExecutionLoop
from core.component_catalog import DEFAULT_COMPONENTS
from core.component_registry import ComponentRegistry, ComponentRuntime
from core.owner_agent_component_runtime import build_owner_agent_execution_component_runtime


class OwnerAgentExecutionComponentTests(unittest.TestCase):
    def test_factory_is_bound_without_being_invoked_and_health_tracks_lifecycle(self):
        calls = []
        def factory(owner_id):
            calls.append(owner_id)
            raise AssertionError("startup must not create owner loops")

        runtime = build_owner_agent_execution_component_runtime(factory)
        self.assertIs(runtime.instance, factory)
        self.assertEqual(calls, [])
        self.assertFalse(runtime.health_check())
        runtime.start()
        self.assertTrue(runtime.health_check())
        self.assertFalse(calls)
        runtime.close()
        self.assertFalse(runtime.health_check())

    def test_registry_orders_factory_after_identity_and_default_agent(self):
        contracts = {item.component_id: item for item in DEFAULT_COMPONENTS}
        registry = ComponentRegistry()
        for component_id in ("identity-access", "provider-runtime"):
            registry.register(ComponentRuntime(
                contracts[component_id], object(), lambda: None, lambda: None, lambda: True
            ))
        default_loop = object()
        # The factory contract depends on identity and the default agent component.
        registry.register(ComponentRuntime(
            contracts["agent-execution"], default_loop, lambda: None, lambda: None, lambda: True
        ))
        factory = lambda owner_id: None
        registry.register(build_owner_agent_execution_component_runtime(factory))
        started = registry.start_all()
        self.assertLess(started.index("agent-execution"), started.index("owner-agent-execution"))
        self.assertLess(started.index("identity-access"), started.index("owner-agent-execution"))

    def test_invalid_factory_and_contract_are_rejected(self):
        with self.assertRaisesRegex(TypeError, "callable"):
            build_owner_agent_execution_component_runtime(None)
        provider = next(item for item in DEFAULT_COMPONENTS if item.component_id == "provider-runtime")
        with self.assertRaisesRegex(ValueError, "owner-agent-execution"):
            build_owner_agent_execution_component_runtime(lambda owner_id: None, contract=provider)


if __name__ == "__main__":
    unittest.main()
