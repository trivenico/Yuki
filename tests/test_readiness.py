import unittest

from core.readiness import build_readiness_report
from core.component_catalog import DEFAULT_COMPONENTS
from core.component_registry import ComponentRegistry, ComponentRuntime


class RuntimeReadinessTests(unittest.TestCase):
    def test_report_contains_platform_and_capability_states(self):
        report = build_readiness_report(
            environ={"YUKI_OLLAMA_MODEL": "example-model"},
            executable_finder=lambda name: None,
            module_finder=lambda name: None,
        )
        data = report.to_dict()
        self.assertTrue(data["python"])
        self.assertTrue(data["operating_system"])
        self.assertTrue(data["architecture"])
        self.assertEqual(data["capabilities"]["ollama_cli"]["status"], "unavailable")
        self.assertEqual(data["capabilities"]["ollama_model_config"]["status"], "configured")
        self.assertEqual(data["capabilities"]["pytorch"]["status"], "unavailable")
        self.assertEqual(data["capabilities"]["onnxruntime"]["status"], "unavailable")

    def test_report_never_claims_model_or_accelerator_is_verified(self):
        report = build_readiness_report(
            environ={"YUKI_OLLAMA_MODEL": "example-model"},
            executable_finder=lambda name: "/usr/bin/ollama",
            module_finder=lambda name: object(),
        ).to_dict()
        self.assertIn("not verified", report["capabilities"]["ollama_cli"]["detail"])
        self.assertIn("not verified", report["capabilities"]["ollama_model_config"]["detail"])
        self.assertIn("not verified", report["capabilities"]["pytorch"]["detail"])
        self.assertIn("not verified", report["capabilities"]["onnxruntime"]["detail"])

    def test_readiness_validates_canonical_component_architecture(self):
        report = build_readiness_report(
            environ={},
            executable_finder=lambda name: None,
            module_finder=lambda name: None,
        )
        architecture = report.capabilities["architecture_catalog"]
        self.assertEqual(architecture.status, "verified")
        self.assertIn("component contracts", architecture.detail)

    def test_component_runtime_readiness_requires_started_healthy_registry(self):
        report = build_readiness_report(
            environ={}, executable_finder=lambda name: None,
            module_finder=lambda name: None,
        )
        self.assertEqual(report.capabilities["component_runtime"].status, "unconfigured")

        contract = next(item for item in DEFAULT_COMPONENTS if item.component_id == "identity-access")
        registry = ComponentRegistry(contracts=(contract,))
        registry.register(ComponentRuntime(
            contract=contract, instance=object(), start=lambda: None,
            close=lambda: None, health_check=lambda: True,
        ))
        registry.start_all()
        healthy = build_readiness_report(
            environ={}, executable_finder=lambda name: None,
            module_finder=lambda name: None, component_registry=registry,
        )
        self.assertEqual(healthy.capabilities["component_runtime"].status, "verified")

    def test_component_runtime_readiness_fails_closed_on_unhealthy_or_raising_probe(self):
        contract = next(item for item in DEFAULT_COMPONENTS if item.component_id == "identity-access")
        for probe in (lambda: False, lambda: (_ for _ in ()).throw(OSError("secret"))):
            registry = ComponentRegistry(contracts=(contract,))
            registry.register(ComponentRuntime(
                contract=contract, instance=object(), start=lambda: None,
                close=lambda: None, health_check=probe,
            ))
            registry.start_all()
            report = build_readiness_report(
                environ={}, executable_finder=lambda name: None,
                module_finder=lambda name: None, component_registry=registry,
            )
            self.assertEqual(report.capabilities["component_runtime"].status, "unavailable")
            self.assertNotIn("secret", report.capabilities["component_runtime"].detail)

    def test_empty_environment_reports_model_unconfigured(self):
        report = build_readiness_report(
            environ={},
            executable_finder=lambda name: None,
            module_finder=lambda name: None,
        )
        self.assertEqual(report.capabilities["ollama_model_config"].status, "unconfigured")


if __name__ == "__main__":
    unittest.main()
