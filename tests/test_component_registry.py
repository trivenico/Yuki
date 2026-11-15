import unittest
from dataclasses import replace

from core.component_catalog import DEFAULT_COMPONENTS
from core.component_registry import ComponentRegistry, ComponentRuntime


class ComponentRegistryTests(unittest.TestCase):
    def setUp(self):
        self.contracts = {item.component_id: item for item in DEFAULT_COMPONENTS}

    def runtime(self, component_id, events):
        contract = self.contracts[component_id]
        return ComponentRuntime(
            contract=contract,
            instance=object(),
            start=lambda: events.append(("start", component_id)),
            close=lambda: events.append(("close", component_id)),
            health_check=lambda: True,
        )

    def test_constructor_rejects_malformed_contract_catalog(self):
        with self.assertRaisesRegex(TypeError, "ComponentContract"):
            ComponentRegistry(contracts=(object(),))
        malformed = replace(self.contracts["identity-access"], component_id="")
        with self.assertRaisesRegex(ValueError, "non-empty string IDs"):
            ComponentRegistry(contracts=(malformed,))

    def test_registration_rejects_missing_runtime_instance(self):
        registry = ComponentRegistry()
        contract = self.contracts["identity-access"]
        runtime = ComponentRuntime(
            contract=contract, instance=None, start=lambda: None,
            close=lambda: None, health_check=lambda: True,
        )
        with self.assertRaisesRegex(ValueError, "instance must not be None"):
            registry.register(runtime)

    def test_registration_rejects_runtime_with_malformed_contract(self):
        events = []
        registry = ComponentRegistry()
        malformed = replace(
            self.runtime("identity-access", events), contract=object()
        )
        with self.assertRaisesRegex(TypeError, "runtime contract"):
            registry.register(malformed)

    def test_registration_rejects_unhashable_or_empty_runtime_component_id(self):
        events = []
        registry = ComponentRegistry()
        for component_id in ([], ""):
            malformed_contract = replace(
                self.contracts["identity-access"], component_id=component_id
            )
            malformed_runtime = replace(
                self.runtime("identity-access", events), contract=malformed_contract
            )
            with self.assertRaisesRegex(ValueError, "non-empty string component ID"):
                registry.register(malformed_runtime)

    def test_health_does_not_probe_registered_components_before_startup(self):
        calls = []
        registry = ComponentRegistry()
        contract = self.contracts["identity-access"]
        registry.register(ComponentRuntime(
            contract=contract, instance=object(), start=lambda: None,
            close=lambda: None, health_check=lambda: calls.append("probe") or True,
        ))

        self.assertEqual(registry.health(), {})
        self.assertEqual(calls, [])

        registry.start_all()
        self.assertEqual(registry.health(), {"identity-access": True})
        self.assertEqual(calls, ["probe"])

    def test_registration_is_frozen_after_startup_begins(self):
        events = []
        registry = ComponentRegistry()
        registry.register(self.runtime("identity-access", events))
        registry.start_all()

        with self.assertRaisesRegex(RuntimeError, "registration is frozen"):
            registry.register(self.runtime("configuration-runtime", events))

        self.assertEqual(registry.start_all(), ("identity-access",))
        self.assertEqual(events, [("start", "identity-access")])

    def test_registration_remains_available_after_dependency_validation_failure(self):
        events = []
        registry = ComponentRegistry()
        registry.register(self.runtime("agent-execution", events))
        with self.assertRaisesRegex(RuntimeError, "missing runtime dependencies"):
            registry.start_all()

        registry.register(self.runtime("identity-access", events))
        registry.register(self.runtime("provider-runtime", events))
        self.assertEqual(
            registry.start_all(),
            ("provider-runtime", "identity-access", "agent-execution"),
        )

    def test_health_rejects_non_boolean_probe_results(self):
        registry = ComponentRegistry()
        runtime = self.runtime("identity-access", [])
        registry.register(replace(runtime, health_check=lambda: 1))
        registry.start_all()

        with self.assertRaisesRegex(TypeError, "must return bool"):
            registry.health()

    def test_health_probe_exception_is_not_reported_as_healthy(self):
        registry = ComponentRegistry()
        runtime = self.runtime("identity-access", [])
        registry.register(replace(
            runtime,
            health_check=lambda: (_ for _ in ()).throw(OSError("probe failed")),
        ))
        registry.start_all()

        with self.assertRaisesRegex(OSError, "probe failed"):
            registry.health()

    def test_starts_dependencies_before_dependents_and_closes_in_reverse(self):
        events = []
        registry = ComponentRegistry()
        registry.register(self.runtime("identity-access", events))
        registry.register(self.runtime("provider-runtime", events))
        registry.register(self.runtime("agent-execution", events))
        started = registry.start_all()
        self.assertEqual(started, ("identity-access", "provider-runtime", "agent-execution"))
        self.assertEqual(registry.resolve("identity-access").__class__, object)
        self.assertEqual(registry.health(), {
            "identity-access": True, "provider-runtime": True, "agent-execution": True
        })
        self.assertEqual(registry.close(), ())
        self.assertEqual(events, [
            ("start", "identity-access"), ("start", "provider-runtime"),
            ("start", "agent-execution"), ("close", "agent-execution"),
            ("close", "provider-runtime"), ("close", "identity-access"),
        ])
        self.assertEqual(registry.close(), ())

    def test_rejects_missing_dependency_before_starting_any_runtime(self):
        events = []
        registry = ComponentRegistry()
        registry.register(self.runtime("agent-execution", events))
        with self.assertRaisesRegex(RuntimeError, "missing runtime dependencies"):
            registry.start_all()
        self.assertEqual(events, [])

    def test_start_failure_rolls_back_started_components(self):
        events = []
        registry = ComponentRegistry()
        registry.register(self.runtime("identity-access", events))
        registry.register(self.runtime("provider-runtime", events))
        dependent = self.runtime("agent-execution", events)
        failing = replace(
            dependent,
            start=lambda: (_ for _ in ()).throw(ValueError("boom")),
            close=lambda: events.append(("close", "agent-execution")),
        )
        registry.register(failing)
        with self.assertRaisesRegex(RuntimeError, "startup failed"):
            registry.start_all()
        self.assertEqual(events, [
            ("start", "identity-access"), ("start", "provider-runtime"),
            ("close", "agent-execution"), ("close", "provider-runtime"),
            ("close", "identity-access")
        ])
        self.assertEqual(registry.started_components, ())

    def test_start_failure_reports_cleanup_failures_without_masking_start_error(self):
        events = []
        registry = ComponentRegistry()
        base = self.runtime("identity-access", events)
        registry.register(replace(
            base, close=lambda: (_ for _ in ()).throw(OSError("cleanup secret"))
        ))
        dependent = self.runtime("provider-runtime", events)
        registry.register(replace(
            dependent, start=lambda: (_ for _ in ()).throw(ValueError("startup secret"))
        ))
        with self.assertRaisesRegex(
            RuntimeError, "startup failed: provider-runtime; cleanup failed for: identity-access"
        ) as raised:
            registry.start_all()
        self.assertIsInstance(raised.exception.__cause__, ValueError)
        self.assertNotIn("secret", str(raised.exception))
        self.assertEqual(registry.startup_cleanup_errors, ("identity-access",))
        self.assertEqual(registry.started_components, ())

    def test_registration_rejects_unknown_contract_and_duplicate(self):
        events = []
        registry = ComponentRegistry()
        registry.register(self.runtime("identity-access", events))
        with self.assertRaisesRegex(ValueError, "already registered"):
            registry.register(self.runtime("identity-access", events))
        unknown = replace(self.contracts["identity-access"], component_id="unknown")
        with self.assertRaisesRegex(ValueError, "not registered"):
            registry.register(replace(self.runtime("identity-access", events), contract=unknown))

    def test_close_failure_does_not_prevent_remaining_cleanup(self):
        events = []
        registry = ComponentRegistry()
        base = self.runtime("identity-access", events)
        broken = replace(base, close=lambda: (_ for _ in ()).throw(OSError("cleanup")))
        registry.register(broken)
        registry.register(self.runtime("configuration-runtime", events))
        registry.start_all()
        self.assertEqual(registry.close(), ("identity-access",))
        self.assertEqual(events, [("start", "identity-access"), ("start", "configuration-runtime"), ("close", "configuration-runtime")])
        self.assertTrue(registry.close() == ())


if __name__ == "__main__":
    unittest.main()
